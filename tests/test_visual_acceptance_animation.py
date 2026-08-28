"""验收工具必须按实际格式解码动画时长，不能被扩展名或懒加载误导。"""

from PIL import Image

from tools.accept_catching_and_collection_views import animation_report


def test_animation_report_loads_webp_frames_before_reading_duration(tmp_path):
    frames = [Image.new("RGB", (24, 24), color) for color in ("red", "green", "blue")]
    source = tmp_path / "source-named-gif.gif"
    output = tmp_path / "composed.gif"
    durations = [70, 60, 90]
    frames[0].save(source, format="WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=0)
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=durations, loop=0)

    report = animation_report(source, output, missing_duration_ms=100)

    assert report["preserved"] is True
    assert report["source"]["durations"] == durations
    assert report["used_missing_duration_fallback"] is False


def test_animation_report_rejects_changed_timing(tmp_path):
    frames = [Image.new("RGB", (24, 24), color) for color in ("red", "blue")]
    source, output = tmp_path / "source.webp", tmp_path / "composed.gif"
    frames[0].save(source, format="WEBP", save_all=True, append_images=frames[1:], duration=[60, 90], loop=0)
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:], duration=[90, 60], loop=0)

    report = animation_report(source, output, missing_duration_ms=100)

    assert report["preserved"] is False
    assert report["expected_output_durations"] == [60, 90]
