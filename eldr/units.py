"""Unit conversions. SH3D stores centimetres; Eldr works in imperial."""

CM_PER_FT = 30.48
SQCM_PER_SQFT = CM_PER_FT * CM_PER_FT  # 929.0304

# Q_sensible (BTU/hr) = SENSIBLE_FACTOR * CFM * deltaT(F); 0.24 * 60 * 0.075.
SENSIBLE_FACTOR = 1.08


def cm_to_ft(length_cm: float) -> float:
    return length_cm / CM_PER_FT


def sqcm_to_sqft(area_cm2: float) -> float:
    return area_cm2 / SQCM_PER_SQFT
