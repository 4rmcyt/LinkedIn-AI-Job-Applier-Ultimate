"""Unit tests for the graceful-shutdown state machine in src.utils.runtime_control."""

from src.utils.runtime_control import RuntimeController, ShutdownState


def test_initial_state_is_running():
    controller = RuntimeController()
    assert controller.shutdown_state == ShutdownState.RUNNING
    assert not controller.is_shutdown_requested()


def test_first_request_transitions_to_draining():
    controller = RuntimeController()
    controller.request_shutdown("test")
    assert controller.shutdown_state == ShutdownState.DRAINING
    assert controller.is_shutdown_requested()


def test_second_request_keeps_draining():
    controller = RuntimeController()
    controller.request_shutdown("first")
    controller.request_shutdown("second")
    assert controller.shutdown_state == ShutdownState.DRAINING
    assert controller.is_shutdown_requested()


def test_request_during_cleanup_does_not_regress_to_draining():
    controller = RuntimeController()
    controller.set_shutdown_state(ShutdownState.CLEANUP)
    controller.request_shutdown("test")
    assert controller.shutdown_state == ShutdownState.CLEANUP
    assert controller.is_shutdown_requested()


def test_set_shutdown_state_resets_for_next_run():
    controller = RuntimeController()
    controller.request_shutdown("test")
    controller.set_shutdown_state(ShutdownState.CLEANUP)
    controller.set_shutdown_state(ShutdownState.DONE)
    # A new run resets the state machine (main.py does this at run start)
    controller.set_shutdown_state(ShutdownState.RUNNING)
    assert controller.shutdown_state == ShutdownState.RUNNING
