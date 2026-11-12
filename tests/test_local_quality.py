"""Sliding local-error score regression tests."""
import numpy as np
import pytest

from laboratory.local_quality import local_error_metrics, experimental_local_flag


@pytest.mark.parametrize("center", [1, 10, 18])
def test_local_displacement_is_visible_without_global_median(center):
    values=np.zeros(20)
    left=max(0,center-1)
    right=min(20,center+2)
    values[left:right]=3.0
    local=local_error_metrics(values,window=3)
    assert np.median(np.abs(values))==0.0
    assert local["local_max_abs_signed_median_khz_eq"]>=3.0
    assert experimental_local_flag(local,threshold=1.5)


def test_signed_negative_and_middle_region():
    values=np.zeros(20)
    values[8:13]=-2.5
    result=local_error_metrics(values,window=5)
    assert result["local_peak_signed_median_khz_eq"]<0
    assert result["local_peak_region"]=="middle"
    assert result["local_middle_median_abs_khz_eq"]>0
    assert result["local_start_median_abs_khz_eq"]==0
    assert result["local_end_median_abs_khz_eq"]==0


def test_unmodified_reference_is_not_flagged():
    result=local_error_metrics([0.0]*20,window=5)
    assert not experimental_local_flag(result)
    assert result["local_max_median_abs_khz_eq"]==0
    assert result["local_peak_start_index"]==0


def test_window_validation_and_no_nan_silent_acceptance():
    with pytest.raises(ValueError):
        local_error_metrics([0.0,1.0])
    with pytest.raises(ValueError):
        local_error_metrics([0.0,float("nan"),0.0,0.0])
    with pytest.raises(ValueError):
        local_error_metrics([0.0]*20,window=2)


def test_baseline_flag_is_not_new_alert():
    baseline=local_error_metrics([2.0]*20,window=5)
    altered=local_error_metrics([3.0]*20,window=5)
    assert experimental_local_flag(baseline)
    assert experimental_local_flag(altered)
    assert not (not experimental_local_flag(baseline)
                and experimental_local_flag(altered))
