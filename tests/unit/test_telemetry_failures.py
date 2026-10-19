"""Telemetry must never change the outcome of the work it observes: not when starting a span fails, not when ending
one fails, and never by hiding an error raised by the observed code."""

from contextlib import contextmanager

import pytest

from src.integrations import observability


class Span:
    def __init__(self, log, fail_enter=False, fail_exit=False, name="span"):
        self.log, self.fail_enter, self.fail_exit, self.name = log, fail_enter, fail_exit, name

    def __enter__(self):
        if self.fail_enter:
            raise RuntimeError("langfuse down")
        self.log.append(f"enter {self.name}")
        return self

    def __exit__(self, *exc_info):
        self.log.append(f"exit {self.name}")
        if self.fail_exit:
            raise RuntimeError("flush failed")
        return True  # a (buggy) span claiming to handle the error must still not swallow it

    def update(self, **kwargs):
        self.log.append(f"update {sorted(kwargs)}")


class Client:
    def __init__(self, span):
        self.span = span

    def start_as_current_observation(self, **kwargs):
        return self.span


@pytest.fixture
def use_client(monkeypatch):
    def _use(span):
        monkeypatch.setattr(observability, "_client", lambda: Client(span))

    return _use


def test_a_working_span_is_entered_and_exited(use_client):
    log = []
    use_client(Span(log))
    with observability.observe("x") as obs:
        obs.update(output="ok")
    assert log == ["enter span", "update ['output']", "exit span"]


def test_a_span_that_cannot_start_does_not_stop_the_work(use_client):
    use_client(Span([], fail_enter=True))
    ran = []
    with observability.observe("x") as obs:
        obs.update(output="ignored")  # a no-op observation
        ran.append(1)
    assert ran == [1]


def test_a_span_that_cannot_end_does_not_turn_a_success_into_a_failure(use_client):
    log = []
    use_client(Span(log, fail_exit=True))
    with observability.observe("x"):
        result = 42
    assert result == 42 and log[-1] == "exit span"


def test_an_error_from_the_observed_code_always_propagates(use_client):
    use_client(Span([]))  # its __exit__ returns True, i.e. claims to have handled the error
    with pytest.raises(ValueError, match="real bug"), observability.observe("x"):
        raise ValueError("real bug")


def test_an_error_from_the_observed_code_propagates_even_if_ending_the_span_also_fails(use_client):
    use_client(Span([], fail_exit=True))
    with pytest.raises(ValueError, match="real bug"), observability.observe("x"):
        raise ValueError("real bug")


def test_incident_trace_survives_a_failure_in_either_of_its_two_context_managers(monkeypatch):
    log = []

    @contextmanager
    def propagate(**kwargs):
        log.append("enter propagate")
        yield
        log.append("exit propagate")

    monkeypatch.setattr(observability, "propagate_attributes", propagate)
    monkeypatch.setattr(observability, "_client", lambda: Client(Span(log, fail_enter=True, name="root")))
    with observability.incident_trace("i-1", "o/r", "f" * 64) as trace:
        trace.update(output="done")
    # the first manager was entered, the second failed: the first is closed again and the work still ran
    assert log == ["enter propagate", "exit propagate"]


def test_incident_trace_with_a_working_client_nests_and_unwinds_in_order(monkeypatch):
    log = []

    @contextmanager
    def propagate(**kwargs):
        log.append("enter propagate")
        yield
        log.append("exit propagate")

    monkeypatch.setattr(observability, "propagate_attributes", propagate)
    monkeypatch.setattr(observability, "_client", lambda: Client(Span(log, name="root")))
    with observability.incident_trace("i-1", "o/r", "f" * 64):
        log.append("work")
    assert log == ["enter propagate", "enter root", "work", "exit root", "exit propagate"]


def test_flush_failures_are_logged_not_raised(monkeypatch):
    class Broken:
        def flush(self):
            raise RuntimeError("network")

    monkeypatch.setattr(observability, "_client", lambda: Broken())
    observability.flush_traces()  # must not raise
