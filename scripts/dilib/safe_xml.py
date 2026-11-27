"""Read Office XML without processing document type declarations."""
from xml.etree.ElementTree import ParseError, TreeBuilder
from xml.parsers import expat


def _reject_doctype(name, system_id, public_id, has_internal_subset):
    raise ParseError("OOXML does not allow DOCTYPE declarations")


class _ExpandedNames(dict):
    """Convert each distinct Expat name to ElementTree notation once per XML part."""
    def __missing__(self, name):
        value = "{" + name if "}" in name else name
        self[name] = value
        return value


def parse_xml(source, namespaces=None, root_namespaces=None):
    """Build an ElementTree from XML bytes, text or a binary stream in one pass.

    Optional dictionaries record the first URI for each prefix in the part and
    the declarations on the root alone, including prefixes used only in text.
    """
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = _reject_doctype
    parser.buffer_text = True
    builder = TreeBuilder()
    names = _ExpandedNames()
    start_node, end_node = builder.start, builder.end

    at_root = True

    def start(name, attrs):
        nonlocal at_root
        at_root = False
        start_node(names[name], {names[k]: v for k, v in attrs.items()} if attrs else attrs)

    def namespace(prefix, uri):
        prefix, uri = prefix or "", uri or ""
        if namespaces is not None:
            namespaces.setdefault(prefix, uri)
        if at_root and root_namespaces is not None:
            root_namespaces[prefix] = uri

    if namespaces is not None or root_namespaces is not None:
        parser.StartNamespaceDeclHandler = namespace
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: end_node(names[name])
    parser.CharacterDataHandler = builder.data
    try:
        if hasattr(source, "read"):
            parser.ParseFile(source)
        else:
            parser.Parse(source, True)
    except expat.ExpatError as exc:
        error = ParseError(str(exc))
        error.code = exc.code
        error.position = (exc.lineno, exc.offset)
        raise error from exc
    return builder.close()
