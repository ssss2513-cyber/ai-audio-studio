"""Deterministic work/timing samples, not GPU speed or voice-quality claims."""
import colab_server


def policy():
    gib = 1024 ** 3
    memory = dict(free=20*gib, total=24*gib, allocated=4*gib, reserved=4*gib, peak=5*gib)
    controller = colab_server.AutoConcurrency(lambda: memory, lambda: None)
    controller.begin()
    controller.calibrated_after_success()
    return controller, memory


def window(controller, seconds, audio=10.0, **overrides):
    epoch, limit = controller.epoch, controller.limit
    count = max(3, limit * 2)
    for index in range(count):
        start = 1000 + (index // limit) * seconds
        sample = dict(epoch=epoch, started=start, finished=start + seconds, audio_seconds=audio,
                      active_before=limit, has_backlog=True)
        sample.update(overrides)
        controller.observe_completion(**sample)


def test_more_free_memory_does_not_raise_concurrency_without_measurements():
    controller, memory = policy()
    for _ in range(100):
        controller.refresh()
    assert controller.limit == 1 and controller.best_rate == 0


def test_two_is_kept_when_four_is_slower_despite_plenty_of_memory():
    controller, _ = policy()
    window(controller, seconds=20)  # 1: 0.5 audio second / wall second
    assert controller.limit == 2
    window(controller, seconds=25)  # 2: 0.8 audio second / wall second
    assert controller.limit == 4
    window(controller, seconds=70)  # 4: about 0.57, worse than 2
    assert controller.limit == controller.best_limit == 2
    assert not controller.searching
    assert set(controller.measurements) == {1, 2, 4}


def test_one_is_kept_when_even_two_is_slower():
    controller, _ = policy()
    window(controller, seconds=20)
    window(controller, seconds=60)
    assert controller.limit == 1 and not controller.searching


def test_ten_is_only_reached_after_each_measured_improvement():
    controller, _ = policy()
    for expected in (2, 4, 6, 8, 10, 10):
        window(controller, seconds=20)
        assert controller.limit == expected
    assert controller.best_limit == 10 and not controller.searching


def test_longer_lines_with_proportional_audio_are_not_mistaken_for_a_slowdown():
    controller, _ = policy()
    window(controller, seconds=20, audio=10)
    window(controller, seconds=40, audio=20)
    assert controller.best_limit == 2 and controller.limit == 4


def test_incomplete_tail_transition_and_invalid_audio_do_not_change_choice():
    controller, _ = policy()
    for overrides in ({'has_backlog': False}, {'epoch': -1}, {'audio_seconds': 0},
                      {'audio_seconds': float('nan')}, {'active_before': 0}):
        window(controller, seconds=20, **overrides)
    assert controller.limit == 1 and not controller.measurements


def test_sustained_regression_rechecks_lower_counts():
    controller, _ = policy()
    window(controller, seconds=20)
    window(controller, seconds=25)
    window(controller, seconds=70)
    window(controller, seconds=60)
    assert controller.limit == 2
    window(controller, seconds=60)
    assert controller.limit == 1 and controller.searching


def test_memory_ceiling_still_wins_and_failed_requests_are_not_samples():
    controller, memory = policy()
    window(controller, seconds=20)
    controller.memory_failure(2)
    assert controller.limit == controller.ceiling == 1
    window(controller, seconds=20)
    assert controller.limit == 1 and controller.memory_retries == 0
    memory['free'] = 0
    assert not controller.can_add(1)


def test_new_job_does_not_measure_idle_time_between_jobs():
    controller, _ = policy()
    controller.observe_completion(epoch=controller.epoch, started=0, finished=20, audio_seconds=10,
                                  active_before=1, has_backlog=True)
    controller.begin()
    assert controller.samples == []


def test_new_memory_pressure_does_not_restore_an_unsafe_old_best():
    controller, memory = policy()
    window(controller, seconds=20)
    window(controller, seconds=20)
    assert controller.limit == 4
    memory['free'] = 3 * controller.GIB
    controller.refresh()
    assert controller.limit == controller.best_limit == 1
    window(controller, seconds=20)
    assert controller.limit == 1
