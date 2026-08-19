from shadow_bridge import _map_strict_progress


def test_strict_progress_leaves_room_for_shadow_post_processing():
    assert _map_strict_progress(0) == 0
    assert _map_strict_progress(50) == 36
    assert _map_strict_progress(100) == 72
