"""Serialize typed nodes and emit CSS Grid rules in the same logical space."""

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from html import escape
from types import MappingProxyType

from spacial_ui.layout import Layout
from spacial_ui.templates import (
    KEY, VOID, Element, Fragment, Node, TemplateError, Text,
    validate_attribute, validate_tag, validate_tree,
)


@dataclass(frozen=True, slots=True)
class Rendered:
    markup: str
    css: str
    events: Mapping[str, Callable[[], None]]


class _Renderer:
    def __init__(self):
        self.styles: list[str] = []
        self.events: dict[str, Callable[[], None]] = {}
        self.grids = 0

    def children(self, nodes: tuple[Node, ...], *, in_grid: bool = False) -> str:
        keys = [node.key for node in nodes if isinstance(node, Element) and node.key is not None]
        if len(keys) != len(set(keys)):
            raise TemplateError("sibling keys must be unique")
        return "".join(self.node(node, in_grid=in_grid) for node in nodes)

    def rules(self, identifier: str, layout: Layout, mode: str) -> str:
        selector = f'[data-sui-grid="{identifier}"]'
        unit = f"calc(100cqw / {layout.columns})"
        if mode == "fit":
            unit = f"min({unit}, calc(100cqh / {layout.rows}))"
        rules = [
            f'{selector}{{--sui-unit:{unit};display:grid;'
            f'grid-template-columns:repeat({layout.columns},var(--sui-unit));'
            f'grid-template-rows:repeat({layout.rows},var(--sui-unit));'
            f'inline-size:calc({layout.columns} * var(--sui-unit));'
            f'block-size:calc({layout.rows} * var(--sui-unit));}}',
            f'{selector}>*{{min-inline-size:0;min-block-size:0;}}',
        ]
        for key, rect in layout.rects.items():
            rules.append(f'{selector}>[data-sui-key="{key}"]{{'
                         f'grid-column:{rect.x + 1}/span {rect.width};'
                         f'grid-row:{rect.y + 1}/span {rect.height};}}')
        return "\n".join(rules)

    def node(self, node: Node, *, in_grid: bool = False) -> str:
        if isinstance(node, Text):
            if in_grid and node.value.strip():
                raise TemplateError("grid children must be keyed elements with placements")
            return escape(node.value, quote=False)
        validate_tag(node.tag)
        if node.key is not None and (not isinstance(node.key, str) or not KEY.fullmatch(node.key)):
            raise TemplateError("invalid element key")
        if node.place is not None and not in_grid:
            raise TemplateError("place is only supported on direct grid children")
        if in_grid and (node.place is None or node.key is None):
            raise TemplateError("every grid child requires a key and place")
        attributes = []
        for name, value in node.attributes:
            validate_attribute(name, value)
            attributes.append(f' {name}' if value is True else f' {name}="{escape(str(value), quote=True)}"')
        if node.key and not node.layout:
            attributes.append(f' data-sui-key="{node.key}"')
        if node.motion:
            attributes.append(f' data-sui-motion="{node.motion.duration}" data-sui-easing="{node.motion.easing}"')
        if node.on_click is not None:
            if node.tag != "button":
                raise TemplateError("on_click requires a semantic button")
            if ("disabled", True) not in node.attributes:
                token = secrets.token_urlsafe(24)
                self.events[token] = node.on_click
                attributes.append(f' data-sui-click="{token}"')
        identifier = None
        if node.layout:
            identifier = f"g{self.grids}"
            self.grids += 1
            placements = {child.key: child.place for child in node.children if isinstance(child, Element)}
            if None in placements or any(place is None for place in placements.values()):
                raise TemplateError("grid children require key and place directives")
            base = node.layout.resolve(placements)
            size = "size" if node.layout.mode == "fit" else "inline-size"
            height = "block-size:100%;" if node.layout.mode == "fit" else ""
            self.styles.append(f'[data-sui-viewport="{identifier}"]{{container:sui-{identifier}/{size};'
                               f'inline-size:100%;{height}display:grid;place-items:center;min-inline-size:0;}}')
            self.styles.append(self.rules(identifier, base, node.layout.mode))
            for point in node.layout.breakpoints:
                variant = node.layout.resolve(placements, container_width=point.min_width)
                self.styles.append(f'@container sui-{identifier} (min-width:{point.min_width}px){{'
                                   + self.rules(identifier, variant, node.layout.mode) + '}')
            attributes.append(f' data-sui-grid="{identifier}"')
        opening = f'<{node.tag}{"".join(attributes)}>'
        result = opening if node.tag in VOID else (
            opening + self.children(node.children, in_grid=node.layout is not None) + f'</{node.tag}>'
        )
        if identifier is not None:
            key = f' data-sui-key="{node.key}"' if node.key else ""
            result = f'<div data-sui-viewport="{identifier}"{key}>{result}</div>'
        return result


def render(fragment: Fragment) -> Rendered:
    if not isinstance(fragment, Fragment):
        raise TypeError("render() requires an html() fragment")
    validate_tree(fragment.children, fragment=False)
    renderer = _Renderer()
    markup = renderer.children(fragment.children)
    return Rendered(markup, "\n".join(renderer.styles), MappingProxyType(renderer.events))
