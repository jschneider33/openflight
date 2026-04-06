"""Standalone helpers for K-LD7 raw ADC (RADC) signal processing."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

RADC_PAYLOAD_BYTES = 3072
SAMPLES_PER_CHANNEL = 256
ADC_MIDPOINT = 32768  # uint16 midpoint for DC offset removal


def parse_radc_payload(payload: bytes) -> dict[str, np.ndarray]:
    """Parse a 3072-byte RADC payload into six uint16 channel arrays.

    Layout (each segment = 256 × uint16 = 512 bytes):
        [0:512]     F1 Freq A — I channel
        [512:1024]  F1 Freq A — Q channel
        [1024:1536] F2 Freq A — I channel
        [1536:2048] F2 Freq A — Q channel
        [2048:2560] F1 Freq B — I channel
        [2560:3072] F1 Freq B — Q channel
    """
    if len(payload) != RADC_PAYLOAD_BYTES:
        raise ValueError(
            f"RADC payload must be {RADC_PAYLOAD_BYTES} bytes, got {len(payload)}"
        )
    seg = 512  # bytes per segment
    return {
        "f1a_i": np.frombuffer(payload[0:seg], dtype=np.uint16).copy(),
        "f1a_q": np.frombuffer(payload[seg : 2 * seg], dtype=np.uint16).copy(),
        "f2a_i": np.frombuffer(payload[2 * seg : 3 * seg], dtype=np.uint16).copy(),
        "f2a_q": np.frombuffer(payload[3 * seg : 4 * seg], dtype=np.uint16).copy(),
        "f1b_i": np.frombuffer(payload[4 * seg : 5 * seg], dtype=np.uint16).copy(),
        "f1b_q": np.frombuffer(payload[5 * seg : 6 * seg], dtype=np.uint16).copy(),
    }


def to_complex_iq(i_channel: np.ndarray, q_channel: np.ndarray) -> np.ndarray:
    """Convert uint16 I/Q arrays to complex float, removing DC offset.

    Uses per-channel mean removal instead of a fixed midpoint, since the
    K-LD7 ADC bias varies across channels and units.
    """
    i_float = i_channel.astype(np.float64) - np.mean(i_channel.astype(np.float64))
    q_float = q_channel.astype(np.float64) - np.mean(q_channel.astype(np.float64))
    return i_float + 1j * q_float


DC_MASK_BINS = 8  # Zero out bins near DC to suppress residual leakage


def compute_spectrum(iq: np.ndarray, fft_size: int = 2048, dc_mask_bins: int = DC_MASK_BINS) -> np.ndarray:
    """Compute magnitude spectrum from complex I/Q with Hann window and zero-padding.

    Args:
        iq: Complex I/Q array (256 samples from RADC)
        fft_size: FFT length (zero-padded if > len(iq))
        dc_mask_bins: Number of bins around DC to zero out (both ends)

    Returns:
        Magnitude spectrum (linear scale), length = fft_size
    """
    windowed = iq * np.hanning(len(iq))
    padded = np.zeros(fft_size, dtype=np.complex128)
    padded[: len(windowed)] = windowed
    fft_result = np.fft.fft(padded)
    magnitude = np.abs(fft_result)
    # Mask DC leakage at both ends of the spectrum
    if dc_mask_bins > 0:
        magnitude[:dc_mask_bins] = 0.0
        magnitude[-dc_mask_bins:] = 0.0
    return magnitude


@dataclass(frozen=True)
class CFARDetection:
    bin_index: int
    magnitude: float
    snr_db: float


def cfar_detect(
    spectrum: np.ndarray,
    guard_cells: int = 4,
    training_cells: int = 16,
    threshold_factor: float = 8.0,
) -> list[CFARDetection]:
    """Ordered-statistic CFAR detection on a magnitude spectrum.

    For each bin, estimates the noise level from surrounding training cells
    (excluding guard cells) and declares a detection if the bin exceeds
    threshold_factor × noise_estimate.

    Args:
        spectrum: Magnitude spectrum (1D array)
        guard_cells: Number of guard cells on each side of the cell under test
        training_cells: Number of training cells on each side (outside guard)
        threshold_factor: Detection threshold as multiple of noise estimate

    Returns:
        List of detections sorted by magnitude (descending)
    """
    n = len(spectrum)
    margin = guard_cells + training_cells
    detections = []

    for i in range(margin, n - margin):
        left_train = spectrum[i - margin : i - guard_cells]
        right_train = spectrum[i + guard_cells + 1 : i + margin + 1]
        training = np.concatenate([left_train, right_train])
        # Use median (OS-CFAR) for robustness against interfering targets
        noise_estimate = np.median(training)

        if noise_estimate <= 0:
            continue

        if spectrum[i] > threshold_factor * noise_estimate:
            snr_db = 10.0 * np.log10(spectrum[i] / noise_estimate)
            detections.append(
                CFARDetection(
                    bin_index=i,
                    magnitude=float(spectrum[i]),
                    snr_db=float(snr_db),
                )
            )

    detections.sort(key=lambda d: d.magnitude, reverse=True)
    return detections


@dataclass(frozen=True)
class RADCDetection:
    frame_index: int
    timestamp: float
    distance_m: float
    velocity_kmh: float
    angle_deg: float
    magnitude: float
    snr_db: float
    bin_index: int


def bin_to_velocity_kmh(bin_index: int, fft_size: int, max_speed_kmh: float) -> float:
    """Convert FFT bin index to velocity in km/h.

    Bins 0..N/2 = 0..+max_speed (outbound).
    Bins N/2..N = -max_speed..0 (inbound, aliased).
    """
    if bin_index <= fft_size // 2:
        return bin_index * max_speed_kmh / (fft_size // 2)
    else:
        return (bin_index - fft_size) * max_speed_kmh / (fft_size // 2)


def estimate_angle_from_phase(
    f1_complex: np.ndarray,
    f2_complex: np.ndarray,
) -> float:
    """Estimate angle from phase difference between two frequency channels.

    Uses cross-correlation phase to estimate the angle of arrival.
    The exact angle-to-phase mapping depends on K-LD7 antenna geometry
    (spacing, wavelength). This returns a proportional estimate that
    needs empirical calibration against known angles.

    Returns:
        Angle estimate in degrees (uncalibrated — proportional to phase diff)
    """
    # Cross-spectral phase
    cross = np.sum(f1_complex * np.conj(f2_complex))
    phase_rad = np.angle(cross)
    # Convert to degrees — scale factor TBD from calibration
    # For K-LD7 at 24 GHz with ~6mm antenna spacing, rough estimate:
    # angle ≈ arcsin(phase / pi) * (180/pi)
    # For now return raw phase in degrees as a proportional estimate
    return float(np.degrees(phase_rad))


# --- Beamforming and spatial filtering ---

# K-LD7 antenna parameters (24 GHz)
WAVELENGTH_M = 3e8 / 24.125e9  # ~12.43 mm
ANTENNA_SPACING_M = 8.0e-3  # ~0.64λ, calibrated against PDAT reference data

# Aliased velocity ranges at RSPI=3 (100 km/h max, 200 km/h unambiguous).
# Golf ball speeds wrap into the negative velocity range:
#   Ball 100-120 mph (161-193 km/h) → aliases to -39 to -7 km/h
#   Club  70-85  mph (113-137 km/h) → aliases to -87 to -63 km/h
BALL_VELOCITY_ALIASED_MIN_KMH = -39.0
BALL_VELOCITY_ALIASED_MAX_KMH = -7.0
CLUB_VELOCITY_ALIASED_MIN_KMH = -87.0
CLUB_VELOCITY_ALIASED_MAX_KMH = -63.0

# At 2048-point FFT with 100 km/h max:
#   bin = (velocity / max_speed) * (fft_size / 2) for positive velocity
#   bin = fft_size + (velocity / max_speed) * (fft_size / 2) for negative velocity
def _velocity_to_bin(velocity_kmh: float, fft_size: int = 2048, max_speed_kmh: float = 100.0) -> int:
    """Convert velocity in km/h to FFT bin index."""
    if velocity_kmh >= 0:
        return int(velocity_kmh * (fft_size // 2) / max_speed_kmh)
    return int(fft_size + velocity_kmh * (fft_size // 2) / max_speed_kmh)


def ball_bin_range(fft_size: int = 2048, max_speed_kmh: float = 100.0) -> tuple[int, int]:
    """Return (lo, hi) FFT bin range for aliased ball velocities (broad default)."""
    return (
        _velocity_to_bin(BALL_VELOCITY_ALIASED_MIN_KMH, fft_size, max_speed_kmh),
        _velocity_to_bin(BALL_VELOCITY_ALIASED_MAX_KMH, fft_size, max_speed_kmh),
    )


def ball_bin_range_from_speed(
    ball_speed_mph: float,
    tolerance_mph: float = 10.0,
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
) -> tuple[int, int]:
    """Return (lo, hi) FFT bin range for a specific ball speed.

    Uses the OPS243-measured ball speed to compute exactly where in the
    aliased spectrum the ball return should appear. Much more precise
    than the broad default range — eliminates club/multipath contamination.

    Args:
        ball_speed_mph: Measured ball speed from OPS243
        tolerance_mph: Search window around the expected velocity (±)
    """
    ball_speed_kmh = ball_speed_mph * 1.609
    unambiguous_range = max_speed_kmh * 2.0
    aliased_kmh = ball_speed_kmh % unambiguous_range
    if aliased_kmh > max_speed_kmh:
        aliased_kmh -= unambiguous_range  # wrap to negative

    lo_vel = aliased_kmh - tolerance_mph * 1.609
    hi_vel = aliased_kmh + tolerance_mph * 1.609

    lo_bin = _velocity_to_bin(lo_vel, fft_size, max_speed_kmh)
    hi_bin = _velocity_to_bin(hi_vel, fft_size, max_speed_kmh)

    # Ensure lo < hi
    if lo_bin > hi_bin:
        lo_bin, hi_bin = hi_bin, lo_bin

    return (lo_bin, hi_bin)


def club_bin_range(fft_size: int = 2048, max_speed_kmh: float = 100.0) -> tuple[int, int]:
    """Return (lo, hi) FFT bin range for aliased club velocities."""
    return (
        _velocity_to_bin(CLUB_VELOCITY_ALIASED_MIN_KMH, fft_size, max_speed_kmh),
        _velocity_to_bin(CLUB_VELOCITY_ALIASED_MAX_KMH, fft_size, max_speed_kmh),
    )


def compute_fft_complex(iq: np.ndarray, fft_size: int = 2048, dc_mask_bins: int = DC_MASK_BINS) -> np.ndarray:
    """Compute complex FFT output (not magnitude) for phase-based processing."""
    windowed = iq * np.hanning(len(iq))
    padded = np.zeros(fft_size, dtype=np.complex128)
    padded[: len(windowed)] = windowed
    result = np.fft.fft(padded)
    if dc_mask_bins > 0:
        result[:dc_mask_bins] = 0.0
        result[-dc_mask_bins:] = 0.0
    return result


def per_bin_angle_deg(
    f1a_fft: np.ndarray,
    f2a_fft: np.ndarray,
    antenna_spacing_m: float = ANTENNA_SPACING_M,
    wavelength_m: float = WAVELENGTH_M,
) -> np.ndarray:
    """Compute angle of arrival at each FFT bin from phase difference between Rx channels.

    Uses the interferometric formula: θ = arcsin(Δφ * λ / (2π * d))
    where Δφ is the phase difference, λ is wavelength, d is antenna spacing.

    Returns array of angles in degrees, one per bin. Bins with no signal return 0.
    """
    cross = f1a_fft * np.conj(f2a_fft)
    phase_diff = np.angle(cross)
    # arcsin argument must be in [-1, 1]
    sin_theta = phase_diff * wavelength_m / (2.0 * np.pi * antenna_spacing_m)
    sin_theta = np.clip(sin_theta, -1.0, 1.0)
    return np.degrees(np.arcsin(sin_theta))


def compute_angle_velocity_map(
    f1a_iq: np.ndarray,
    f2a_iq: np.ndarray,
    fft_size: int = 2048,
    steer_angles_deg: np.ndarray | None = None,
    antenna_spacing_m: float = ANTENNA_SPACING_M,
    wavelength_m: float = WAVELENGTH_M,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Compute angle-velocity power map via conventional beamforming.

    Steers a 2-element array across angles and computes beamformed power
    at each (angle, velocity_bin) pair.

    Returns:
        (power_map, steer_angles, velocity_bins)
        power_map shape: (n_angles, fft_size)
    """
    if steer_angles_deg is None:
        steer_angles_deg = np.arange(-90, 91, 1.0)

    f1a_fft = compute_fft_complex(f1a_iq, fft_size=fft_size)
    f2a_fft = compute_fft_complex(f2a_iq, fft_size=fft_size)

    power_map = np.zeros((len(steer_angles_deg), fft_size))

    for i, angle_deg in enumerate(steer_angles_deg):
        angle_rad = np.radians(angle_deg)
        # Phase shift to steer beam to this angle
        steering_phase = 2.0 * np.pi * antenna_spacing_m * np.sin(angle_rad) / wavelength_m
        # Apply steering vector to second channel and sum
        steered = f1a_fft + f2a_fft * np.exp(-1j * steering_phase)
        power_map[i, :] = np.abs(steered) ** 2

    return power_map, steer_angles_deg, np.arange(fft_size)


@dataclass(frozen=True)
class SpatialDetection:
    """Detection with per-bin angle from interferometry."""
    frame_index: int
    timestamp: float
    velocity_kmh: float
    angle_deg: float  # per-bin angle from phase difference
    magnitude: float
    snr_db: float
    bin_index: int


def find_impact_frames(
    frames: list[dict],
    fft_size: int = 2048,
    min_velocity_bin: int = 150,
    energy_threshold: float = 3.0,
) -> list[int]:
    """Find frames with sudden high-velocity energy (impact events).

    Looks for frames where the high-velocity portion of the spectrum
    has significantly more energy than the surrounding frames.
    """
    energies = []
    for frame in frames:
        radc = frame.get("radc")
        if radc is None:
            energies.append(0.0)
            continue
        channels = parse_radc_payload(radc) if isinstance(radc, bytes) else radc
        iq = to_complex_iq(channels["f1a_i"], channels["f1a_q"])
        spec = compute_spectrum(iq, fft_size=fft_size)
        # Energy in high-velocity bins only
        high_vel_energy = float(np.sum(spec[min_velocity_bin: fft_size // 2] ** 2))
        energies.append(high_vel_energy)

    energies = np.array(energies)
    if np.median(energies) <= 0:
        return []

    # Frames where high-velocity energy exceeds median by threshold factor
    median_energy = np.median(energies[energies > 0])
    impact_indices = []
    for i, e in enumerate(energies):
        if e > energy_threshold * median_energy:
            impact_indices.append(i)
    return impact_indices


def process_radc_frame_spatial(
    frame: dict,
    frame_index: int,
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 4.0,
    cfar_guard: int = 4,
    cfar_training: int = 16,
    bin_range: tuple[int, int] | None = None,
) -> list[SpatialDetection]:
    """Process one RADC frame with per-bin angle estimation.

    Args:
        bin_range: Optional (lo, hi) to restrict CFAR to a specific FFT bin
                   range (e.g. ball or club velocity band). If None, runs on
                   the full spectrum.
    """
    radc_raw = frame.get("radc")
    if radc_raw is None:
        return []

    channels = parse_radc_payload(radc_raw) if isinstance(radc_raw, bytes) else radc_raw

    f1a_iq = to_complex_iq(channels["f1a_i"], channels["f1a_q"])
    f2a_iq = to_complex_iq(channels["f2a_i"], channels["f2a_q"])

    spectrum = compute_spectrum(f1a_iq, fft_size=fft_size)

    # Band-limited CFAR: zero out everything outside the target bin range
    if bin_range is not None:
        masked = np.zeros_like(spectrum)
        lo, hi = bin_range
        masked[lo:hi] = spectrum[lo:hi]
        spectrum = masked

    cfar_hits = cfar_detect(
        spectrum,
        guard_cells=cfar_guard,
        training_cells=cfar_training,
        threshold_factor=cfar_threshold,
    )

    # Complex FFTs for per-bin angle
    f1a_fft = compute_fft_complex(f1a_iq, fft_size=fft_size)
    f2a_fft = compute_fft_complex(f2a_iq, fft_size=fft_size)
    angles = per_bin_angle_deg(f1a_fft, f2a_fft)

    timestamp = float(frame["timestamp"])
    detections = []
    for hit in cfar_hits:
        velocity = bin_to_velocity_kmh(hit.bin_index, fft_size, max_speed_kmh)
        detections.append(
            SpatialDetection(
                frame_index=frame_index,
                timestamp=timestamp,
                velocity_kmh=velocity,
                angle_deg=float(angles[hit.bin_index]),
                magnitude=hit.magnitude,
                snr_db=hit.snr_db,
                bin_index=hit.bin_index,
            )
        )
    return detections


def extract_launch_angle(
    frames: list[dict],
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 2.5,
    impact_energy_threshold: float = 3.0,
    angle_offset_deg: float = 0.0,
    ops243_ball_speed_mph: float | None = None,
    speed_tolerance_mph: float = 10.0,
) -> list[dict]:
    """Extract vertical launch angle per shot from RADC frames.

    Pipeline:
    1. Find impact frames (high-velocity energy spikes)
    2. Group consecutive impacts into shot events
    3. For each shot, run band-limited CFAR in the ball velocity range
    4. Per-bin interferometric angle estimation on ball detections
    5. SNR²-weighted average angle (heavily favors strongest returns)
    6. Apply angle offset

    Args:
        ops243_ball_speed_mph: If provided (live path), narrows the velocity
            search to a tight band around this speed. This eliminates
            club/multipath contamination and works for any club/player.
            If None (offline analysis), uses the broad default ball range.
        speed_tolerance_mph: Search window ± around ops243_ball_speed_mph.

    Returns a list of shot dicts, one per detected shot. Each contains
    launch_angle_deg, ball_speed_mph, confidence, and supporting data.
    Returns empty list if no shots found.
    """
    min_velocity_bin = 150  # skip low-velocity body/clutter
    impact_indices = find_impact_frames(
        frames, fft_size=fft_size,
        min_velocity_bin=min_velocity_bin,
        energy_threshold=impact_energy_threshold,
    )
    if not impact_indices:
        return []

    # Group consecutive impact frames into shot events
    shot_groups: list[list[int]] = []
    for idx in impact_indices:
        if not shot_groups or idx - shot_groups[-1][-1] > 5:
            shot_groups.append([idx])
        else:
            shot_groups[-1].append(idx)

    # Velocity band: narrow (OPS243-anchored) or broad (offline default)
    if ops243_ball_speed_mph is not None:
        b_lo, b_hi = ball_bin_range_from_speed(
            ops243_ball_speed_mph, speed_tolerance_mph, fft_size, max_speed_kmh,
        )
    else:
        b_lo, b_hi = ball_bin_range(fft_size, max_speed_kmh)

    results = []
    for shot_idx, impact_group in enumerate(shot_groups):
        # Expand to impact ±1 before, +2 after (ball appears slightly after impact)
        frame_set = set()
        for idx in impact_group:
            for offset in range(-1, 3):
                fi = idx + offset
                if 0 <= fi < len(frames):
                    frame_set.add(fi)

        ball_dets = []
        for fi in sorted(frame_set):
            dets = process_radc_frame_spatial(
                frames[fi], frame_index=fi,
                fft_size=fft_size, max_speed_kmh=max_speed_kmh,
                cfar_threshold=cfar_threshold,
                bin_range=(b_lo, b_hi),
            )
            ball_dets.extend(dets)

        if not ball_dets:
            continue

        # SNR²-weighted angle (heavily favors strongest returns)
        total_snr2 = sum(d.snr_db ** 2 for d in ball_dets)
        if total_snr2 <= 0:
            continue
        weighted_angle = sum(d.angle_deg * d.snr_db ** 2 for d in ball_dets) / total_snr2
        corrected_angle = weighted_angle + angle_offset_deg

        # Ball speed from aliased velocity (unwrap)
        real_speeds_mph = [(200.0 + d.velocity_kmh) / 1.609 for d in ball_dets]
        avg_speed_mph = float(np.mean(real_speeds_mph))

        # Confidence
        avg_snr = float(np.mean([d.snr_db for d in ball_dets]))
        frame_count = len(set(d.frame_index for d in ball_dets))
        frame_score = min(frame_count / 3.0, 1.0)
        snr_score = min(avg_snr / 15.0, 1.0)
        det_score = min(len(ball_dets) / 10.0, 1.0)
        confidence = round(frame_score * 0.35 + snr_score * 0.35 + det_score * 0.30, 2)

        angles = [d.angle_deg for d in ball_dets]
        angle_std = float(np.std(angles)) if len(angles) > 1 else 0.0

        results.append({
            "shot_index": shot_idx,
            "launch_angle_deg": round(corrected_angle, 1),
            "raw_angle_deg": round(weighted_angle, 1),
            "angle_offset_deg": angle_offset_deg,
            "ball_speed_mph": round(avg_speed_mph, 1),
            "confidence": confidence,
            "detection_count": len(ball_dets),
            "frame_count": frame_count,
            "angle_std_deg": round(angle_std, 1),
            "avg_snr_db": round(avg_snr, 1),
            "impact_frames": impact_group,
            "detections": ball_dets,
        })

    return results


def process_radc_frame(
    frame: dict,
    frame_index: int,
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 8.0,
    cfar_guard: int = 4,
    cfar_training: int = 16,
) -> list[RADCDetection]:
    """Process one RADC frame: parse → FFT → CFAR → physical units.

    Uses F1A channel as primary, F2A for angle estimation.
    """
    radc_raw = frame.get("radc")
    if radc_raw is None:
        return []

    if isinstance(radc_raw, bytes):
        channels = parse_radc_payload(radc_raw)
    else:
        channels = radc_raw

    f1a = to_complex_iq(channels["f1a_i"], channels["f1a_q"])
    f2a = to_complex_iq(channels["f2a_i"], channels["f2a_q"])

    spectrum = compute_spectrum(f1a, fft_size=fft_size)
    cfar_hits = cfar_detect(
        spectrum,
        guard_cells=cfar_guard,
        training_cells=cfar_training,
        threshold_factor=cfar_threshold,
    )

    angle_deg = estimate_angle_from_phase(f1a, f2a)
    timestamp = float(frame["timestamp"])

    detections = []
    for hit in cfar_hits:
        velocity = bin_to_velocity_kmh(hit.bin_index, fft_size, max_speed_kmh)
        detections.append(
            RADCDetection(
                frame_index=frame_index,
                timestamp=timestamp,
                distance_m=0.0,  # RADC gives velocity, not range — set from FMCW chirp later
                velocity_kmh=velocity,
                angle_deg=angle_deg,
                magnitude=hit.magnitude,
                snr_db=hit.snr_db,
                bin_index=hit.bin_index,
            )
        )

    return detections


def compare_radc_vs_pdat(
    radc_detections: list[RADCDetection],
    pdat: list[dict],
) -> dict:
    """Compare our RADC FFT detections against the module's PDAT output.

    Returns a summary dict for logging / CSV export.
    """
    pdat_speeds = [abs(p.get("speed", 0)) for p in pdat if p]
    pdat_mags = [p.get("magnitude", 0) for p in pdat if p]
    radc_velocities = [abs(d.velocity_kmh) for d in radc_detections]
    radc_mags = [d.magnitude for d in radc_detections]

    return {
        "radc_count": len(radc_detections),
        "pdat_count": len(pdat),
        "radc_max_velocity_kmh": max(radc_velocities) if radc_velocities else 0.0,
        "pdat_max_speed_kmh": max(pdat_speeds) if pdat_speeds else 0.0,
        "radc_max_magnitude": max(radc_mags) if radc_mags else 0.0,
        "pdat_max_magnitude": max(pdat_mags) if pdat_mags else 0.0,
    }
