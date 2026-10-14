import concurrent.futures
import unittest

from spacial_ui import (
    CallbackError, Component, EventError, Session, StaleEventError, State,
    UnknownEventError, html,
)


class Counter(Component):
    count = State(0)
    notes = State([])

    def increment(self):
        self.count += 1

    def render(self):
        return html(t'<section><p>Count: {self.count}</p><button on_click={self.increment}>Add</button></section>')


class ComponentTests(unittest.TestCase):
    def test_defaults_are_per_instance_and_copied(self):
        first, second = Counter(), Counter()
        first.count += 1
        first.notes.append('first')
        self.assertEqual(second.count, 0)
        self.assertEqual(second.notes, [])
        self.assertIsInstance(Counter.count, State)

    def test_retained_child_state_and_template_reevaluation(self):
        class Parent(Component):
            def __init__(self):
                self.child = Counter()
            def render(self):
                return html(t'<main>{self.child}</main>')
        parent = Parent()
        session = Session(parent)
        first = session.render()
        second = session.dispatch(first.event_ids[0], first.version)
        self.assertIn('Count: 1', second.markup)
        self.assertEqual(parent.child.count, 1)
        self.assertGreater(second.version, first.version)
        self.assertNotEqual(first.event_ids, second.event_ids)

    def test_sessions_have_independent_state_and_event_registries(self):
        first, second = Session(Counter()), Session(Counter())
        a, b = first.render(), second.render()
        with self.assertRaises(UnknownEventError):
            second.dispatch(a.event_ids[0], b.version)
        first.dispatch(a.event_ids[0], a.version)
        self.assertEqual(second.component.count, 0)
        self.assertIn('Count: 0', second.render().markup)

    def test_stale_replayed_unknown_and_invalid_events(self):
        session = Session(Counter())
        first = session.render()
        session.dispatch(first.event_ids[0], first.version)
        with self.assertRaises(StaleEventError):
            session.dispatch(first.event_ids[0], first.version)
        fresh = session.render()
        for event, version in [('', fresh.version), ('event', True), ('event', 0), (None, 1)]:
            with self.subTest(event=event, version=version), self.assertRaises(EventError):
                session.dispatch(event, version)
        with self.assertRaises(UnknownEventError):
            session.dispatch('increment', fresh.version)
        self.assertEqual(session.component.count, 1)

    def test_concurrent_duplicate_only_runs_once(self):
        session = Session(Counter())
        snapshot = session.render()
        def send():
            try:
                session.dispatch(snapshot.event_ids[0], snapshot.version)
                return 'ok'
            except StaleEventError:
                return 'stale'
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(lambda _: send(), range(2))), ['ok', 'stale'])
        self.assertEqual(session.component.count, 1)

    def test_callback_failure_invalidates_events_without_exposing_details(self):
        class Failing(Counter):
            def increment(self):
                self.count += 1
                raise RuntimeError('private exception details')
        session = Session(Failing())
        snapshot = session.render()
        with self.assertRaises(CallbackError) as error:
            session.dispatch(snapshot.event_ids[0], snapshot.version)
        self.assertNotIn('private', str(error.exception))
        with self.assertRaises(StaleEventError):
            session.dispatch(snapshot.event_ids[0], snapshot.version)
        # Event callbacks are ordinary Python, not automatic transactions.
        self.assertEqual(session.component.count, 1)
        self.assertIn('Count: 1', session.render().markup)

    def test_handlers_reject_arguments_async_generators_and_returned_values(self):
        def needs_argument(value):
            pass
        async def asynchronous():
            pass
        def generator():
            yield 1
        class AsyncCallable:
            async def __call__(self):
                pass
        class Handler(Component):
            def __init__(self, callback):
                self.callback = callback
            def render(self):
                return html(t'<button on_click={self.callback}>Run</button>')
        for callback in (needs_argument, asynchronous, generator, AsyncCallable()):
            with self.subTest(callback=callback), self.assertRaises(EventError):
                Session(Handler(callback)).render()
        session = Session(Handler(lambda: 'unsupported result'))
        snapshot = session.render()
        with self.assertRaises(CallbackError):
            session.dispatch(snapshot.event_ids[0], snapshot.version)

    def test_disabled_buttons_register_no_event(self):
        class Disabled(Counter):
            def render(self):
                return html(t'<button disabled on_click={self.increment}>Unavailable</button>')
        snapshot = Session(Disabled()).render()
        self.assertEqual(snapshot.event_ids, ())
        self.assertNotIn('data-sui-click', snapshot.markup)


if __name__ == '__main__':
    unittest.main()
