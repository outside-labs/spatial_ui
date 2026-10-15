"""An ordinary dashboard and a small fit scene share the same spatial model."""

from importlib.resources import files

from spatial_ui.components import Component, State
from spatial_ui.layout import Breakpoint, Grid, at, below, right_of
from spatial_ui.motion import Transition
from spatial_ui.templates import html


class Dashboard(Component):
    count = State(0)
    compact = State(False)
    moved = State(False)
    motion_enabled = State(True)

    def increment(self):
        self.count += 1

    def toggle_canvas(self):
        self.compact = not self.compact

    def move_panel(self):
        self.moved = not self.moved

    def toggle_motion(self):
        self.motion_enabled = not self.motion_enabled

    def render(self):
        grid = Grid(4, 19, mode="reflow", breakpoints=(Breakpoint(760, 12, 9, {
            "navigation": at(0, 0, 3, 9),
            "welcome": at(3, 0, 9, 2),
            "activity": at(3, 2, 4, 3),
            "notes": right_of("activity", width=5, height=3),
            "scene": below("activity", width=9, height=4),
        }),))
        motion = Transition(260 if self.motion_enabled else 0)
        tile = at(5 if self.moved else 1, 1, 2, 2)
        canvas_class = "canvas compact" if self.compact else "canvas"
        canvas_label = "Use wide canvas" if self.compact else "Use compact canvas"
        motion_label = "Enable motion" if not self.motion_enabled else "Reduce motion"
        return html(t'''
            <div class="workspace" motion={motion}>
                <a class="skip-link" href="#workspace-content">Skip to workspace</a>
                <header class="masthead">
                    <a href="/" class="brand"><span class="brand-mark" aria-hidden="true">s.</span>spatial_ui</a>
                    <span class="studio">OUTSIDE LABS / EXPERIMENT 001</span>
                    <span class="local-badge">LOCAL PREVIEW</span>
                </header>
                <div class="intro">
                    <div><p class="eyebrow">A LITTLE ROOM TO THINK</p><h1>Your everyday space.</h1>
                        <p class="intro-copy">Real elements. Shared coordinates. Everything in its place.</p></div>
                    <div class="toolbar" aria-label="Preview controls">
                        <button type="button" key="canvas-control" aria-pressed={self.compact} on_click={self.toggle_canvas}>{canvas_label}</button>
                        <button type="button" key="motion-control" aria-pressed={not self.motion_enabled} on_click={self.toggle_motion}>{motion_label}</button>
                    </div>
                </div>
                <div class={canvas_class}>
                    <main id="workspace-content" layout={grid}>
                        <nav class="sidebar panel" key="navigation" place={at(0, 0, 4, 2)} aria-label="Workspace">
                            <div><p class="eyebrow">YOUR WORKSPACE</p><a href="#welcome" aria-current="page">Overview <span aria-hidden="true">↗</span></a>
                                <a href="#activity">Activity</a><a href="#notes">Field notes</a><a href="#scene">The playground</a></div>
                            <div class="sidebar-foot"><span class="dot" aria-hidden="true"></span> A work in progress.</div>
                        </nav>
                        <section class="welcome panel" id="welcome" key="welcome" place={below("navigation", width=4, height=3)}>
                            <p class="eyebrow">01 / THE OVERVIEW</p><h2>A place for what’s next.</h2>
                            <p>A small dashboard, made of objects that know where they belong.</p>
                            <span class="tag">PYTHON + T-STRINGS + CSS GRID</span>
                        </section>
                        <section class="activity panel" id="activity" key="activity" place={below("welcome", width=4, height=3)}>
                            <div class="card-top"><p class="eyebrow">02 / SMALL STEPS</p><span class="dot" aria-hidden="true"></span></div>
                            <p class="count" aria-live="polite" aria-atomic="true">{self.count}</p><h2>Things set in motion.</h2>
                            <button class="primary" type="button" key="add-action" on_click={self.increment}>Add an action <span aria-hidden="true">+</span></button>
                        </section>
                        <section class="notes panel" id="notes" key="notes" place={below("activity", width=4, height=4)}>
                            <p class="eyebrow">03 / FIELD NOTES</p><h2>Keep a thought close.</h2>
                            <label for="scratch-note">A note for later</label>
                            <textarea id="scratch-note" key="scratch-note" rows="3" placeholder="What are you working on?" aria-describedby="note-hint"></textarea>
                            <p id="note-hint" class="hint">A scratchpad for this page. Your typing stays put when other cards update.</p>
                        </section>
                        <section class="playground panel" id="scene" key="scene" place={below("notes", width=4, height=7)}>
                            <div class="playground-heading"><div><p class="eyebrow">04 / THE PLAYGROUND</p><h2>Same grid. A different possibility.</h2></div>
                                <button type="button" key="move-control" on_click={self.move_panel}>Move the tile <span aria-hidden="true">→</span></button></div>
                            <div class="fit-shell">
                                <div class="tile-world" layout={Grid(8, 4)} aria-label="Eight by four square-cell scene">
                                    <div class="tile" key="tile" place={tile}><span aria-hidden="true">✳</span><span class="tile-caption">HELLO, WORLD</span></div>
                                </div>
                            </div>
                            <p class="hint">One unit on both axes. A fixed scene, contained in the available space.</p>
                        </section>
                    </main>
                </div>
                <footer class="page-footer"><span>spatial_ui <span aria-hidden="true">/</span> A spatial UI engine in Python.</span><span>BUILT AROUND RELATIONSHIPS.</span></footer>
            </div>
        ''')


def demo_stylesheet() -> str:
    return files("spatial_ui").joinpath("static", "demo.css").read_text(encoding="utf-8")
