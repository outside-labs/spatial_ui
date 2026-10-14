"""Compile t-strings into a deliberately small, inert HTML vocabulary."""

import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from string.templatelib import Template
from urllib.parse import urlsplit

from spacial_ui.geometry import Rect
from spacial_ui.layout import Centered, Grid, Placement, Relative


class TemplateError(ValueError):
    pass


VOID = frozenset("br col hr img input wbr".split())
TAGS = frozenset((
    "a abbr address article aside b blockquote button caption code colgroup dd del "
    "details dfn dialog div dl dt em fieldset figcaption figure footer form "
    "h1 h2 h3 h4 h5 h6 header hgroup i kbd label legend li main mark nav ol "
    "optgroup option output p pre progress q s samp section select small span "
    "strong sub summary sup table tbody td textarea tfoot th thead time tr u ul var"
).split()) | VOID
BOOLEAN = frozenset("autofocus checked disabled hidden inert multiple open readonly required selected".split())
ATTRS = frozenset((
    "id class title lang dir role tabindex name value type for placeholder autocomplete "
    "minlength maxlength min max step pattern size cols rows wrap download target rel "
    "href src alt width height loading decoding action method enctype form formaction "
    "formmethod scope colspan rowspan headers datetime cite start reversed label"
).split()) | BOOLEAN
URLS = frozenset({"href", "src", "action", "formaction", "cite"})
KEY = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]*\Z")
DATA = re.compile(r"(?:aria|data)-[a-z][a-z0-9_.-]*\Z")
RESTRICTED_CHILDREN = {
    "ul": {"li"}, "ol": {"li"}, "dl": {"dt", "dd"},
    "table": {"caption", "colgroup", "thead", "tbody", "tfoot"},
    "thead": {"tr"}, "tbody": {"tr"}, "tfoot": {"tr"}, "tr": {"td", "th"},
    "colgroup": {"col"}, "select": {"option", "optgroup"}, "optgroup": {"option"},
    "textarea": set(), "option": set(),
}
PHRASING = frozenset((
    "a abbr b br button code del dfn em i img input kbd label mark output progress "
    "q s samp select small span strong sub sup textarea time u var wbr"
).split())
PHRASING_PARENTS = PHRASING - {"button", "select", "textarea", "output"} | {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "button"
}


def validate_tag(tag: str) -> None:
    if tag not in TAGS:
        raise TemplateError(f"unsupported HTML element {tag!r}")


def validate_attribute(name: str, value: str | bool) -> None:
    if name.startswith("data-sui-") or (name not in ATTRS and not DATA.fullmatch(name)):
        raise TemplateError(f"unsupported or reserved attribute {name!r}")
    if name in URLS:
        if not isinstance(value, str):
            raise TemplateError(f"{name} requires a URL string")
        if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
            raise TemplateError("URL whitespace and control characters must be percent-encoded")
        if "\\" in value or value.startswith("//"):
            raise TemplateError("backslashes and protocol-relative URLs are unsupported")
        try:
            scheme = urlsplit(value).scheme.lower()
        except ValueError as error:
            raise TemplateError("invalid URL") from error
        allowed = {"", "http", "https", "mailto", "tel"} if name == "href" else {"", "http", "https"}
        if scheme not in allowed:
            raise TemplateError(f"unsupported URL scheme {scheme!r}")


@dataclass(frozen=True, slots=True)
class Text:
    value: str


@dataclass(frozen=True, slots=True)
class Element:
    tag: str
    attributes: tuple[tuple[str, str | bool], ...] = ()
    children: tuple[Text | Element, ...] = ()
    key: str | None = None
    layout: Grid | None = None
    place: Placement | None = None
    on_click: Callable[[], None] | None = None


type Node = Text | Element


@dataclass(frozen=True, slots=True)
class Fragment:
    children: tuple[Node, ...]


def validate_tree(nodes: tuple[Node, ...], ancestors: tuple[str, ...] = (), *,
                  fragment: bool = True) -> None:
    required = {
        "li": {"ul", "ol"}, "dt": {"dl"}, "dd": {"dl"},
        "caption": {"table"}, "colgroup": {"table"}, "col": {"colgroup"},
        "thead": {"table"}, "tbody": {"table"}, "tfoot": {"table"},
        "tr": {"thead", "tbody", "tfoot"}, "td": {"tr"}, "th": {"tr"},
        "optgroup": {"select"}, "option": {"select", "optgroup"},
    }
    for node in nodes:
        parent = ancestors[-1] if ancestors else None
        if isinstance(node, Text):
            if node.value.strip() and parent in RESTRICTED_CHILDREN and parent not in {"textarea", "option"}:
                raise TemplateError(f"{parent!r} cannot contain text directly")
            continue
        validate_tag(node.tag)
        if parent in RESTRICTED_CHILDREN and node.tag not in RESTRICTED_CHILDREN[parent]:
            raise TemplateError(f"{node.tag!r} cannot be a child of {parent!r}")
        if parent in PHRASING_PARENTS and node.tag not in PHRASING:
            raise TemplateError(f"{parent!r} only accepts phrasing content")
        if node.tag in required and (parent is not None or not fragment) and parent not in required[node.tag]:
            raise TemplateError(f"{node.tag!r} requires a supported parent")
        if node.tag in {"a", "button", "form", "label"} and node.tag in ancestors:
            raise TemplateError(f"nested {node.tag!r} elements are unsupported")
        if {"a", "button"} & set(ancestors) and node.tag in {"a", "button", "input", "select", "textarea"}:
            raise TemplateError("interactive controls cannot contain other interactive controls")
        validate_tree(node.children, ancestors + (node.tag,), fragment=fragment)


class Renderable(ABC):
    @abstractmethod
    def render(self) -> Fragment:
        """Produce a fresh template tree."""


@dataclass
class _Builder:
    tag: str
    attributes: tuple[tuple[str, str | bool], ...]
    directives: dict[str, object]
    children: list[Node] = field(default_factory=list)

    def finish(self) -> Element:
        return Element(self.tag, self.attributes, tuple(self.children), **self.directives)


class _Compiler(HTMLParser):
    def __init__(self, template: Template):
        super().__init__(convert_charrefs=True)
        self.values = template.values
        self.prefix = f"sui{uuid.uuid4().hex}x"
        self.marker = re.compile(re.escape(self.prefix) + r"(\d+)z")
        self.used: set[int] = set()
        self.stack: list[_Builder] = []
        self.roots: list[Node] = []
        for interpolation in template.interpolations:
            if interpolation.conversion is not None or interpolation.format_spec:
                raise TemplateError("format values before interpolation; conversions and format specs are unsupported")
        self.source = "".join(
            literal + (f"{self.prefix}{index}z" if index < len(self.values) else "")
            for index, literal in enumerate(template.strings)
        )

    def value(self, match: re.Match) -> object:
        index = int(match.group(1))
        if index in self.used:
            raise TemplateError("an interpolation was consumed twice")
        self.used.add(index)
        return self.values[index]

    def parts(self, text: str) -> list[object]:
        parts: list[object] = []
        start = 0
        for match in self.marker.finditer(text):
            parts.extend((text[start:match.start()], self.value(match)))
            start = match.end()
        parts.append(text[start:])
        return parts

    def append(self, node: Node) -> None:
        if self.stack:
            self.stack[-1].children.append(node)
        else:
            self.roots.append(node)

    def content(self, value: object, seen: frozenset[int] = frozenset()) -> None:
        if value is None:
            return
        if isinstance(value, Renderable):
            value = value.render()
        if isinstance(value, Template):
            value = html(value)
        if isinstance(value, Fragment):
            for node in value.children:
                self.append(node)
        elif isinstance(value, (tuple, list)):
            if id(value) in seen:
                raise TemplateError("cyclic child sequence")
            for child in value:
                self.content(child, seen | {id(value)})
        elif isinstance(value, (str, int, float)):
            self.append(Text(str(value)))
        else:
            raise TemplateError(f"unsupported child type {type(value).__name__}")

    def handle_starttag(self, tag, attrs):
        validate_tag(tag)
        names: set[str] = set()
        attributes = []
        directives = {}
        for name, raw in attrs:
            if name in names:
                raise TemplateError(f"duplicate attribute {name!r}")
            names.add(name)
            if name in {"layout", "place", "on_click"}:
                match = self.marker.fullmatch(raw or "")
                if not match:
                    raise TemplateError(f"{name} requires one typed interpolation")
                value = self.value(match)
                expected = {"layout": (Grid,), "place": (Rect, Relative, Centered), "on_click": ()}[name]
                if (name == "on_click" and not callable(value)) or (name != "on_click" and not isinstance(value, expected)):
                    raise TemplateError(f"invalid {name} directive")
                directives[name] = value
                continue
            match = self.marker.fullmatch(raw or "")
            if match:
                value = self.value(match)
            elif raw is None:
                value = True
            else:
                parts = self.parts(raw)
                if any(type(part) not in (str, int, float, bool) for part in parts):
                    raise TemplateError("attribute parts must be scalar values")
                value = "".join(str(part) for part in parts)
            if name == "key":
                if not isinstance(value, str) or not KEY.fullmatch(value):
                    raise TemplateError("key must start with a letter and contain only letters, digits, '_' or '-'")
                directives["key"] = value
                continue
            if name in BOOLEAN:
                if value is None or value is False:
                    continue
                if type(value) not in (str, bool):
                    raise TemplateError("boolean attributes require bool, None, or literal HTML values")
                value = True
            elif value is None:
                continue
            elif type(value) not in (str, int, float, bool):
                raise TemplateError("attribute values must be scalar")
            else:
                value = str(value)
            validate_attribute(name, value)
            attributes.append((name, value))
        if tag in VOID:
            if "layout" in directives:
                raise TemplateError("void elements cannot define a layout")
            self.append(_Builder(tag, tuple(attributes), directives).finish())
        else:
            self.stack.append(_Builder(tag, tuple(attributes), directives))

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1].tag != tag:
            raise TemplateError(f"unexpected closing tag {tag!r}; close every non-void element explicitly")
        self.append(self.stack.pop().finish())

    def handle_startendtag(self, tag, attrs):
        if tag not in VOID:
            raise TemplateError("only void elements may use self-closing syntax")
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        for part in self.parts(data):
            self.content(part)

    def handle_comment(self, data):
        if self.marker.search(data):
            raise TemplateError("interpolation in comments is unsupported")

    def handle_decl(self, decl):
        raise TemplateError("document declarations are unsupported in fragments")

    def handle_pi(self, data):
        raise TemplateError("processing instructions are unsupported")

    def unknown_decl(self, data):
        raise TemplateError("declarations are unsupported")


def html(template: Template) -> Fragment:
    """Compile explicit HTML literals; dynamic strings always become text nodes."""
    if not isinstance(template, Template):
        raise TypeError("html() requires a Python 3.14 t-string, not str")
    compiler = _Compiler(template)
    try:
        compiler.feed(compiler.source)
        compiler.close()
    except (AssertionError, IndexError) as error:
        raise TemplateError("malformed HTML") from error
    if compiler.stack:
        raise TemplateError(f"unclosed tag {compiler.stack[-1].tag!r}")
    if len(compiler.used) != len(template.values):
        raise TemplateError("interpolation in an unsupported HTML context")
    validate_tree(tuple(compiler.roots))
    return Fragment(tuple(compiler.roots))
