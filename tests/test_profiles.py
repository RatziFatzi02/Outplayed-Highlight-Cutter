from pathlib import Path

from outplayed_highlight_cutter.ffmpeg import RenderOptions
from outplayed_highlight_cutter.profiles import RenderProfile, decode_profiles, encode_profiles


def test_render_profiles_round_trip() -> None:
    profiles = [
        RenderProfile(
            "AMD AV1", RenderOptions(encoder="av1_amf", quality=17, show_markers=True, font_path=Path("font.ttf")),
            transition="crossfade", transition_seconds=0.5,
        )
    ]
    restored = decode_profiles(encode_profiles(profiles))
    assert restored[0].name == "AMD AV1"
    assert restored[0].options.encoder == "av1_amf"
    assert restored[0].options.font_path == Path("font.ttf")
    assert restored[0].transition == "crossfade"
    assert restored[0].transition_seconds == 0.5
