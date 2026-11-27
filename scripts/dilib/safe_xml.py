"""Read Office XML without processing document type declarations."""
from xml.etree.ElementTree import ParseError, TreeBuilder
from xml.parsers import expat


def _reject_doctype(name, system_id, public_id, has_internal_subset):
    raise ParseError("OOXML does not allow DOCTYPE declarations")


def parse_xml(source, namespaces=None, root_namespaces=None):
    """Build an ElementTree from XML bytes, text or a binary stream in one pass.

    Optional dictionaries record the first URI for each prefix in the part and
    the declarations on the root alone, including prefixes used only in text.
    """
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartDoctypeDeclHandler = _reject_doctype
    parser.buffer_text = True
    builder = TreeBuilder()
    names = {}

    def expanded(name):
        if name not in names:
            names[name] = "{" + name if "}" in name else name
        return names[name]

    at_root = True

    def start(name, attrs):
        nonlocal at_root
        at_root = False
        builder.start(expanded(name), {expanded(k): v for k, v in attrs.items()})

    def namespace(prefix, uri):
        prefix, uri = prefix or "", uri or ""
        if namespaces is not None:
            namespaces.setdefault(prefix, uri)
        if at_root and root_namespaces is not None:
            root_namespaces[prefix] = uri

    if namespaces is not None or root_namespaces is not None:
        parser.StartNamespaceDeclHandler = namespace
    parser.StartElementHandler = start
    parser.EndElementHandler = lambda name: builder.end(expanded(name))
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
