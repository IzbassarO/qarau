import shutil
from pathlib import Path
from typing import Any

import pytest

from qarau_pipeline.interfaces import StageError
from qarau_pipeline.probe import is_vfr, parse, probe_video

CFR = [i / 30 for i in range(60)]


def stream(**kw: Any) -> dict[str, Any]:
    s = {"codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p",
         "avg_frame_rate": "30/1", "r_frame_rate": "30/1", "duration": "3.0"}  # fmt: skip
    return {"streams": [{**s, **kw}], "format": {"duration": "3.0"}}


def test_sdr() -> None:
    v = parse(stream(color_transfer="bt709", color_primaries="bt709"), CFR)
    assert not v.is_hdr and v.bit_depth == 8 and v.rotation_deg == 0 and not v.is_vfr
    assert (v.display_width, v.display_height, v.avg_fps) == (1920, 1080, 30.0)


def test_displaymatrix_minus_90() -> None:
    # iPhone portrait as ffprobe prints it (ffmpeg >= 5): counter-clockwise -90
    v = parse(stream(side_data_list=[{"side_data_type": "Display Matrix", "rotation": -90}]), CFR)
    assert v.rotation_deg == 90 and (v.display_width, v.display_height) == (1080, 1920)


def test_tags_rotate() -> None:
    v = parse(stream(tags={"rotate": "270"}), CFR)
    assert v.rotation_deg == 270 and (v.display_width, v.display_height) == (1080, 1920)
    assert parse(stream(tags={"rotate": "180"}), CFR).display_width == 1920


def test_pq() -> None:
    v = parse(stream(codec_name="hevc", pix_fmt="yuv420p10le", color_transfer="smpte2084",
                     color_primaries="bt2020"), CFR)  # fmt: skip
    assert v.is_hdr and v.bit_depth == 10


def test_hlg() -> None:
    v = parse(stream(pix_fmt="yuv420p10le", color_transfer="arib-std-b67"), CFR)
    assert v.is_hdr


def test_bt2020_needs_10_bit() -> None:
    assert parse(stream(pix_fmt="yuv420p10le", color_primaries="bt2020"), CFR).is_hdr
    assert not parse(stream(color_primaries="bt2020"), CFR).is_hdr


def test_duration_from_container() -> None:
    j = stream()
    del j["streams"][0]["duration"]
    j["format"]["duration"] = "20.2"
    assert parse(j, CFR).duration_s == 20.2
    with pytest.raises(StageError, match="no video stream"):
        parse({"streams": [], "format": {}}, [])


def test_vfr_tolerance() -> None:
    assert not is_vfr([round(t, 3) for t in CFR])  # 1 ms timebase rounding is not VFR
    assert is_vfr([i / 10 for i in range(10)] + [1 + i / 30 for i in range(30)])
    assert not is_vfr([0.0])


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_unreadable_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(b"not a video" * 100)
    with pytest.raises(StageError, match="cannot read video bad.mp4"):
        probe_video(bad)
