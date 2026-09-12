# problem_id: 5cb294c18288f6ea4e50d59fd9831610232b6df28cc1a84b7d23e3d1eb5c7bb1
# source_language: python
# phase: 3
# entrypoint: validate_build
# verified: False

def validate_build(schemas: list[tuple], root: tuple, operations: list[tuple]) -> int:
    from collections import deque

    # Descriptor equality check (recursive)
    def desc_eq(d1, d2):
        if d1 == d2:
            return True
        if isinstance(d1, tuple) and isinstance(d2, tuple):
            if d1[0] != d2[0]:
                return False
            if d1[0] == "record":
                # ("record", s)
                return d1[1] == d2[1]
            elif d1[0] == "array":
                # ("array", d, c)
                return desc_eq(d1[1], d2[1]) and d1[2] == d2[2]
            else:
                return False
        return False

    # Flattened layout memo:
    # schema_flattened[s] = {
    #   'segments': list of segments,
    #   'length': total number of fields
    # }
    # segment: ('field', (name, descriptor)) or ('repeat', (t, r, schema_flattened[t]))
    schema_flattened = {}

    def flatten_schema(s):
        if s in schema_flattened:
            return schema_flattened[s]
        layout = schemas[s]
        segments = []
        total_len = 0
        for term in layout:
            if term[0] == "field":
                # ("field", name, d)
                name, d = term[1], term[2]
                segments.append(('field', (name, d)))
                total_len += 1
            else:
                # ("repeat", t, r)
                t, r = term[1], term[2]
                # flatten t recursively
                inner = flatten_schema(t)
                segments.append(('repeat', (t, r, inner)))
                total_len += inner['length'] * r
        schema_flattened[s] = {'segments': segments, 'length': total_len}
        return schema_flattened[s]

    # flatten all schemas on demand
    # root schema flattening
    flatten_schema(root[1])

    # Given a flattened layout (segments) and index i (0-based), return (name, descriptor)
    # segments is a list of segments as above
    # i < total length guaranteed by caller
    def get_field(segments, i):
        # segments is a list of segments
        # Each segment is either:
        # - ('field', (name, d)) length 1
        # - ('repeat', (t, r, inner)) length inner['length'] * r
        # We find which segment contains the i-th field and recurse if repeat
        for seg in segments:
            if seg[0] == 'field':
                if i == 0:
                    return seg[1]
                i -= 1
            else:
                # repeat
                t, r, inner = seg[1]
                seg_len = inner['length'] * r
                if i < seg_len:
                    # find which repetition
                    pos_in_rep = i % inner['length']
                    return get_field(inner['segments'], pos_in_rep)
                i -= seg_len
        # Should never reach here if i < total length
        raise IndexError("Index out of bounds in get_field")

    # Check if a descriptor is primitive ("int" or "text")
    def is_primitive(d):
        return d == "int" or d == "text"

    # Container stack element:
    # For records:
    #   {
    #     'type': 'record',
    #     'schema': s,
    #     'segments': segments,
    #     'length': length,
    #     'pos': current field index (0-based),
    #   }
    # For arrays:
    #   {
    #     'type': 'array',
    #     'elem_desc': d,
    #     'capacity': c,
    #     'pos': current element count (0-based),
    #   }

    # Initialize stack with root record container
    stack = []
    root_schema = root[1]
    root_flat = schema_flattened[root_schema]
    stack.append({
        'type': 'record',
        'schema': root_schema,
        'segments': root_flat['segments'],
        'length': root_flat['length'],
        'pos': 0,
    })

    # Helper to get current container or None if none
    def current_container():
        return stack[-1] if stack else None

    # Helper to get next field info in current record container
    # Returns (name, descriptor) or None if no fields remain
    def next_field(container):
        if container['pos'] >= container['length']:
            return None
        return get_field(container['segments'], container['pos'])

    # Helper to advance position in current container by k fields (for record)
    # or k elements (for array)
    # Returns True if successful, False if not enough capacity/fields remain
    def advance(container, k):
        if container['type'] == 'record':
            if container['pos'] + k > container['length']:
                return False
            container['pos'] += k
            return True
        else:
            # array
            if container['pos'] + k > container['capacity']:
                return False
            container['pos'] += k
            return True

    # Validate and process each operation
    for idx, op in enumerate(operations, 1):
        c = current_container()
        if c is None:
            # No open container, any operation invalid
            return idx
        if op[0] == "put":
            # ("put", name, p)
            name, p = op[1], op[2]
            if c['type'] == 'record':
                # next field must exist
                nf = next_field(c)
                if nf is None:
                    return idx
                nf_name, nf_desc = nf
                # name and descriptor must match exactly
                if name != nf_name:
                    return idx
                if not desc_eq(nf_desc, p):
                    return idx
                # advance position by 1
                if not advance(c, 1):
                    return idx
            else:
                # array
                # name must be None
                if name is not None:
                    return idx
                # capacity must remain
                if c['pos'] >= c['capacity']:
                    return idx
                # descriptor must equal element descriptor
                if not desc_eq(c['elem_desc'], p):
                    return idx
                # advance position by 1
                if not advance(c, 1):
                    return idx

        elif op[0] == "open":
            # ("open", name, d)
            name, d = op[1], op[2]
            if c['type'] == 'record':
                # next field must exist
                nf = next_field(c)
                if nf is None:
                    return idx
                nf_name, nf_desc = nf
                # name and descriptor must match exactly
                if name != nf_name:
                    return idx
                if not desc_eq(nf_desc, d):
                    return idx
                # advance position by 1
                if not advance(c, 1):
                    return idx
                # open new container with descriptor d
                if isinstance(d, tuple):
                    if d[0] == "record":
                        s = d[1]
                        flat = flatten_schema(s)
                        stack.append({
                            'type': 'record',
                            'schema': s,
                            'segments': flat['segments'],
                            'length': flat['length'],
                            'pos': 0,
                        })
                    elif d[0] == "array":
                        elem_desc = d[1]
                        capacity = d[2]
                        stack.append({
                            'type': 'array',
                            'elem_desc': elem_desc,
                            'capacity': capacity,
                            'pos': 0,
                        })
                    else:
                        # invalid descriptor type for open
                        return idx
                else:
                    # d is primitive, cannot open primitive
                    return idx
            else:
                # array
                # name must be None
                if name is not None:
                    return idx
                # capacity must remain
                if c['pos'] >= c['capacity']:
                    return idx
                # descriptor must equal element descriptor
                if not desc_eq(c['elem_desc'], d):
                    return idx
                # advance position by 1
                if not advance(c, 1):
                    return idx
                # open new container with descriptor d
                if isinstance(d, tuple):
                    if d[0] == "record":
                        s = d[1]
                        flat = flatten_schema(s)
                        stack.append({
                            'type': 'record',
                            'schema': s,
                            'segments': flat['segments'],
                            'length': flat['length'],
                            'pos': 0,
                        })
                    elif d[0] == "array":
                        elem_desc = d[1]
                        capacity = d[2]
                        stack.append({
                            'type': 'array',
                            'elem_desc': elem_desc,
                            'capacity': capacity,
                            'pos': 0,
                        })
                    else:
                        # invalid descriptor type for open
                        return idx
                else:
                    # d is primitive, cannot open primitive
                    return idx

        elif op[0] == "default":
            # ("default", k)
            k = op[1]
            if c['type'] == 'record':
                # at least k fields remain
                if c['pos'] + k > c['length']:
                    return idx
                # every one of those k fields must have primitive descriptor
                # check fields from pos to pos+k-1
                for offset in range(k):
                    nf = get_field(c['segments'], c['pos'] + offset)
                    _, nf_desc = nf
                    if not is_primitive(nf_desc):
                        return idx
                # advance position by k
                if not advance(c, k):
                    return idx
            else:
                # array
                # element descriptor must be primitive
                if not is_primitive(c['elem_desc']):
                    return idx
                # capacity must remain at least k
                if c['pos'] + k > c['capacity']:
                    return idx
                # advance position by k
                if not advance(c, k):
                    return idx

        elif op[0] == "close":
            # close current container
            if c['type'] == 'record':
                # can close only if all fields filled
                if c['pos'] != c['length']:
                    return idx
                stack.pop()
            else:
                # array can close at any length
                stack.pop()
        else:
            # unknown operation
            return idx

    # After all operations:
    # If no container open, return 0
    # Else return len(operations) + 1
    if stack:
        return len(operations) + 1
    return 0
