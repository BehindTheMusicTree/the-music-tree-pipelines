import polars as pl
import pytest

from common.youtube_regions import REGION_POLICY_DTYPE, check_region_policies, region_policy, with_region_policy


@pytest.mark.parametrize(
    "restriction, expected",
    [
        (None, {"mode": "unrestricted", "countries": []}),
        ({"allowed": ["GB", "FR", "FR"]}, {"mode": "allowed", "countries": ["FR", "GB"]}),
        ({"allowed": []}, {"mode": "allowed", "countries": []}),
        ({"blocked": []}, {"mode": "blocked", "countries": []}),
        ({"blocked": ["AL"]}, {"mode": "blocked", "countries": ["AL"]}),
    ],
)
def test_region_policy(restriction, expected):
    details = {} if restriction is None else {"regionRestriction": restriction}
    assert region_policy({"contentDetails": details}) == expected


@pytest.mark.parametrize(
    "restriction",
    [
        {},
        {"allowed": [], "blocked": []},
        {"allowed": ["fr"]},
        {"blocked": [None]},
        {"allowed": "FR"},
        {"allowed": ["XX"]},
    ],
)
def test_rejects_invalid_policy(restriction):
    with pytest.raises(ValueError):
        region_policy({"contentDetails": {"regionRestriction": restriction}})


def test_unknown_legacy_policy_and_missing_video():
    assert region_policy(None) is None
    frame = with_region_policy(pl.DataFrame({"youtube_video_id": ["video"]}).lazy()).collect()
    assert frame["youtube_region_restriction"].to_list() == [None]
    check_region_policies(frame)
    frame = pl.DataFrame(
        {"youtube_video_id": [None], "youtube_region_restriction": [{"mode": "allowed", "countries": []}]},
        schema_overrides={"youtube_region_restriction": REGION_POLICY_DTYPE},
    )
    with pytest.raises(ValueError):
        check_region_policies(frame)
