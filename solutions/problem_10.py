# problem_id: 6eca8a9120e08a6e5ef9287cd54499cb6e04ff87927037c5f656dc5e2ff4e08f
# source_language: python
# phase: 2
# entrypoint: capture_binders
# verified: True

import ast
import copy
from typing import List, Set, Tuple


def _collect_bound_names(node: ast.AST) -> Set[str]:
    """
    Return the set of identifiers that *bind* a name in the given node.
    This includes function / class definitions, arguments, comprehension
    targets, with‑as names, except‑as names, import‑as names, etc.
    """
    bound: Set[str] = set()

    class BinderVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, n: ast.FunctionDef):
            bound.add(n.name)
            for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs:
                bound.add(a.arg)
            if n.args.vararg:
                bound.add(n.args.vararg.arg)
            if n.args.kwarg:
                bound.add(n.args.kwarg.arg)
            self.generic_visit(n)

        def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef):
            self.visit_FunctionDef(n)  # same handling

        def visit_ClassDef(self, n: ast.ClassDef):
            bound.add(n.name)
            self.generic_visit(n)

        def visit_Lambda(self, n: ast.Lambda):
            for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs:
                bound.add(a.arg)
            if n.args.vararg:
                bound.add(n.args.vararg.arg)
            if n.args.kwarg:
                bound.add(n.args.kwarg.arg)
            self.generic_visit(n)

        def visit_Name(self, n: ast.Name):
            # Name nodes are *not* binders unless they appear in a binding context.
            pass

        def visit_ExceptHandler(self, n: ast.ExceptHandler):
            if n.name:
                bound.add(n.name)
            self.generic_visit(n)

        def visit_With(self, n: ast.With):
            for item in n.items:
                if item.optional_vars and isinstance(item.optional_vars, ast.Name):
                    bound.add(item.optional_vars.id)
            self.generic_visit(n)

        def visit_AsyncWith(self, n: ast.AsyncWith):
            self.visit_With(n)

        def visit_Import(self, n: ast.Import):
            for alias in n.names:
                bound.add(alias.asname or alias.name.split('.')[0])

        def visit_ImportFrom(self, n: ast.ImportFrom):
            for alias in n.names:
                bound.add(alias.asname or alias.name)

        def visit_comprehension(self, n: ast.comprehension):
            # target can be a tuple/list of names; we walk it to collect all.
            for name in _extract_target_names(n.target):
                bound.add(name)

        def visit_Starred(self, n: ast.Starred):
            # Starred can appear in assignment targets (e.g., a, *rest = ...).
            # The inner value is visited by generic_visit which will handle Names.
            self.generic_visit(n)

    BinderVisitor().visit(node)
    return bound


def _extract_target_names(target: ast.AST) -> List[str]:
    """
    Given an assignment target (Name, Tuple, List, etc.) return all identifier
    strings that are bound by it.
    """
    names: List[str] = []

    class TargetVisitor(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            names.append(n.id)

        def generic_visit(self, n: ast.AST):
            super().generic_visit(n)

    TargetVisitor().visit(target)
    return names


def _free_names(node: ast.AST) -> Set[str]:
    """
    Return the set of identifiers that are *read* (Load context) inside `node`.
    """
    free: Set[str] = set()

    class FreeVisitor(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name):
            if isinstance(n.ctx, ast.Load):
                free.add(n.id)

        def generic_visit(self, n: ast.AST):
            super().generic_visit(n)

    FreeVisitor().visit(node)
    return free


def _rename_in_node(node: ast.AST, renames: dict) -> ast.AST:
    """
    Return a copy of `node` where every Name with id in `renames` is replaced
    by a new Name with the corresponding new identifier.
    """
    class Renamer(ast.NodeTransformer):
        def visit_Name(self, n: ast.Name):
            if n.id in renames:
                new = copy.deepcopy(n)
                new.id = renames[n.id]
                return new
            return n

    return Renamer().visit(copy.deepcopy(node))


def capture_binders(
    nodes: List[ast.AST],
    expr_root: ast.AST,
    target: str,
    replacement_root: ast.AST,
) -> List[ast.AST]:
    """
    Perform a capture‑avoiding substitution of `target` with `replacement_root`
    inside the list of AST nodes `nodes`.

    * `target` – the identifier (as a string) that should be replaced.
    * `replacement_root` – an AST fragment that will replace each occurrence
      of `target` (in Load context) that is not shadowed by a binder.
    * `expr_root` – the root node of the *whole* expression that contains
      `nodes`.  It is used only to compute the set of free names in the
      replacement so that we can avoid accidental capture.

    The function walks the tree, respects Python's lexical scoping rules,
    and renames identifiers inside `replacement_root` when they would be
    captured by a binder that appears in the surrounding scope.
    """
    # Free identifiers used inside the replacement – these are the ones we must
    # protect from being captured.
    repl_free = _free_names(replacement_root)

    # Helper that decides whether a Name node should be replaced.
    def should_replace(name_node: ast.Name, bound_stack: List[Set[str]]) -> bool:
        # Only replace Load occurrences of the exact target name.
        if not isinstance(name_node.ctx, ast.Load):
            return False
        if name_node.id != target:
            return False
        # If any enclosing scope already binds `target`, we must not replace.
        for scope in reversed(bound_stack):
            if target in scope:
                return False
        return True

    class Substituter(ast.NodeTransformer):
        def __init__(self):
            # Stack of sets of bound identifiers for each lexical scope.
            self.bound_stack: List[Set[str]] = []

        def _enter_scope(self, node: ast.AST):
            # Compute the identifiers bound *by* this node.
            bound_here = _collect_bound_names(node)
            self.bound_stack.append(bound_here)

            # If any of the bound names clash with free names of the replacement,
            # we need to alpha‑convert the replacement for this scope.
            clash = bound_here & repl_free
            if clash:
                # Build a deterministic fresh name for each clashing identifier.
                renames = {}
                for name in clash:
                    # Simple fresh‑name strategy: append a unique suffix.
                    # In a real compiler we would need a global counter; here we
                    # use the node's id to keep it deterministic.
                    fresh = f"{name}_capture_{id(node)}"
                    renames[name] = fresh
                # Store the renamed replacement for this scope.
                self._current_replacement = _rename_in_node(replacement_root, renames)
            else:
                self._current_replacement = replacement_root

        def _exit_scope(self):
            self.bound_stack.pop()
            # Reset to the original replacement when leaving the scope.
            self._current_replacement = replacement_root

        # -----------------------------------------------------------------
        # Scope‑introducing nodes – we wrap their generic_visit with the
        # enter/exit bookkeeping.
        # -----------------------------------------------------------------
        def visit_FunctionDef(self, node: ast.FunctionDef):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
            return self.visit_FunctionDef(node)

        def visit_ClassDef(self, node: ast.ClassDef):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        def visit_Lambda(self, node: ast.Lambda):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        def visit_IfExp(self, node: ast.IfExp):
            # IfExp does not introduce a new scope.
            return self.generic_visit(node)

        def visit_ListComp(self, node: ast.ListComp):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        def visit_SetComp(self, node: ast.SetComp):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        def visit_DictComp(self, node: ast.DictComp):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        def visit_GeneratorExp(self, node: ast.GeneratorExp):
            self._enter_scope(node)
            self.generic_visit(node)
            self._exit_scope()
            return node

        # -----------------------------------------------------------------
        # The actual substitution point.
        # -----------------------------------------------------------------
        def visit_Name(self, node: ast.Name):
            if should_replace(node, self.bound_stack):
                # Use a deepcopy of the (possibly renamed) replacement.
                return copy.deepcopy(self._current_replacement)
            return node

        # -----------------------------------------------------------------
        # For all other nodes we just recurse.
        # -----------------------------------------------------------------
        def generic_visit(self, node: ast.AST):
            return super().generic_visit(node)

    transformer = Substituter()
    new_nodes = [transformer.visit(copy.deepcopy(n)) for n in nodes]
    # Ensure locations are sane (helps later processing / pretty‑printing).
    return [ast.fix_missing_locations(n) for n in new_nodes]
