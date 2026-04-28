"""K-LD7 raw ADC (RADC) signal processing for the openflight package.

Core functions for FFT-based velocity detection and phase interferometry
angle extraction from K-LD7 24 GHz radar raw ADC data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

RADC_PAYLOAD_BYTES = 3072
SAMPLES_PER_CHANNEL = 256

DC_MASK_BINS = 8  # Zero out bins near DC to suppress residual leakage

# Half-width (in FFT bins) of the neighborhood around the per-frame peak
# bin used for the magnitude²-weighted centroid angle. A real ball peak
# spreads across a handful of bins (Hann-window leakage + intra-frame
# Doppler smear); 16 bins on either side comfortably covers the peak
# shape without picking up noise elsewhere in the ball band.
CENTROID_SEARCH_BINS = 16

# K-LD7 antenna parameters (24 GHz)
WAVELENGTH_M = 3e8 / 24.125e9  # ~12.43 mm
ANTENNA_SPACING_M = 8.0e-3  # ~0.64λ, calibrated against PDAT reference data


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


def bin_to_velocity_kmh(bin_index: int, fft_size: int, max_speed_kmh: float) -> float:
    """Convert FFT bin index to velocity in km/h.

    Bins 0..N/2 = 0..+max_speed (outbound).
    Bins N/2..N = -max_speed..0 (inbound, aliased).
    """
    if bin_index <= fft_size // 2:
        return bin_index * max_speed_kmh / (fft_size // 2)
    else:
        return (bin_index - fft_size) * max_speed_kmh / (fft_size // 2)


def _velocity_to_bin(velocity_kmh: float, fft_size: int = 2048, max_speed_kmh: float = 100.0) -> int:
    """Convert velocity in km/h to FFT bin index."""
    if velocity_kmh >= 0:
        return int(velocity_kmh * (fft_size // 2) / max_speed_kmh)
    return int(fft_size + velocity_kmh * (fft_size // 2) / max_speed_kmh)


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


def find_impact_frames(
    frames: list[dict],
    fft_size: int = 2048,
    min_velocity_bin: int = 150,
    energy_threshold: float = 3.0,
    ball_bin_lo: int | None = None,
    ball_bin_hi: int | None = None,
) -> list[int]:
    """Find frames with sudden high-velocity energy (impact events).

    Looks for frames where the high-velocity portion of the spectrum
    has significantly more energy than the surrounding frames.

    Checks both positive-velocity bins (min_velocity_bin to N/2, for club)
    and the ball velocity band (ball_bin_lo:ball_bin_hi if provided,
    otherwise the full negative-velocity half N/2 to N). Golf ball speeds
    alias into the negative velocity range at RSPI=100 km/h, so checking
    only positive bins misses ball impacts.
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
        # Energy in positive high-velocity bins (club swing)
        pos_energy = float(np.sum(spec[min_velocity_bin: fft_size // 2] ** 2))
        # Energy in aliased negative-velocity bins (ball)
        if ball_bin_lo is not None and ball_bin_hi is not None:
            neg_energy = float(np.sum(spec[ball_bin_lo:ball_bin_hi] ** 2))
        else:
            neg_energy = float(np.sum(spec[fft_size // 2 + min_velocity_bin:] ** 2))
        energies.append(pos_energy + neg_energy)

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


def extract_launch_angle(
    frames: list[dict],
    fft_size: int = 2048,
    max_speed_kmh: float = 100.0,
    cfar_threshold: float = 2.5,
    impact_energy_threshold: float = 3.0,
    angle_offset_deg: float = 0.0,
    ops243_ball_speed_mph: float | None = None,
    speed_tolerance_mph: float = 10.0,
    orientation: str | None = None,
    ops_bin_outlier_tol: int = 25,
    ops_bin_outlier_penalty: float = 10.0,
    centroid_floor_frac: float = 0.5,
) -> list[dict]:
    """Extract vertical launch angle per shot from RADC frames.

    Pipeline:
    1. Find impact frames (high-velocity energy spikes)
    2. Group consecutive impacts into shot events
    3. For each shot, run band-limited CFAR in the ball velocity range
    4. Per-bin interferometric angle estimation on ball detections.
       The per-frame angle is the magnitude²-weighted centroid of the
       per-bin angles inside the spectral peak (bins whose magnitude
       exceeds `centroid_floor_frac` of the peak), rather than the raw
       angle at the single peak bin. For range-spread targets like a
       golf ball whose energy spreads across multiple FFT bins due to
       Hann-window leakage and intra-frame Doppler smear, this is much
       more robust to noise than reading a single bin.
    5. SNR²-weighted average angle across frames. When the OPS243 ball
       speed is supplied, frames whose peak bin is more than
       ops_bin_outlier_tol away from the OPS-expected bin have their
       weight reduced by ops_bin_outlier_penalty (default 10×). This
       downweights persistent clutter stripes that sit inside the ball
       velocity band but far from the actual ball location.
    6. Apply angle offset

    Args:
        ops243_ball_speed_mph: If provided (live path), narrows the velocity
            search to a tight band around this speed. This eliminates
            club/multipath contamination and works for any club/player.
            If None (offline analysis), uses the broad default ball range.
        speed_tolerance_mph: Search window ± around ops243_ball_speed_mph.
        ops_bin_outlier_tol: When ops243_ball_speed_mph is provided, frames
            whose peak bin is more than this many bins from the
            OPS-expected bin are downweighted in the final average.
            Has no effect when ops243_ball_speed_mph is None.
        ops_bin_outlier_penalty: Weight divisor for outlier frames
            (default 10×). Set to 1.0 to disable the soft check.
        centroid_floor_frac: Bins inside the ball band whose magnitude
            is at least this fraction of the peak are included in the
            per-frame magnitude²-weighted angle centroid (default 0.5,
            i.e. all bins above the half-power point of the peak). Set
            to 1.0 to revert to single-peak-bin angle extraction.

    Returns a list of shot dicts, one per detected shot. Each contains
    launch_angle_deg, ball_speed_mph, confidence, and supporting data.
    Returns empty list if no shots found.
    """
    # Velocity band: narrow (OPS243-anchored) or broad (offline default)
    if ops243_ball_speed_mph is not None:
        b_lo, b_hi = ball_bin_range_from_speed(
            ops243_ball_speed_mph, speed_tolerance_mph, fft_size, max_speed_kmh,
        )
        # Where the ball *should* peak, given the OPS243 speed. Used as a
        # soft anchor for the SNR²-weighted average below.
        ball_speed_kmh = ops243_ball_speed_mph * 1.609
        unambiguous_range = max_speed_kmh * 2.0
        aliased_kmh = ball_speed_kmh % unambiguous_range
        if aliased_kmh > max_speed_kmh:
            aliased_kmh -= unambiguous_range
        ops_expected_bin: int | None = _velocity_to_bin(
            aliased_kmh, fft_size, max_speed_kmh,
        )
    else:
        # Broad default ball velocity range for offline analysis.
        # Ball 100-120 mph aliases to -39 to -7 km/h at RSPI=3 (100 km/h max).
        b_lo = _velocity_to_bin(-39.0, fft_size, max_speed_kmh)
        b_hi = _velocity_to_bin(-7.0, fft_size, max_speed_kmh)
        ops_expected_bin = None

    min_velocity_bin = 150  # skip low-velocity body/clutter
    impact_indices = find_impact_frames(
        frames, fft_size=fft_size,
        min_velocity_bin=min_velocity_bin,
        energy_threshold=impact_energy_threshold,
        ball_bin_lo=b_lo,
        ball_bin_hi=b_hi,
    )
    if not impact_indices:
        import logging
        logging.getLogger("openflight.kld7.radc").info(
            "[KLD7-RADC] No impact frames found (energy_threshold=%.1f, ball_bins=%d-%d, %d frames)",
            impact_energy_threshold, b_lo, b_hi, len(frames),
        )
        return []

    # Group consecutive impact frames into shot events
    shot_groups: list[list[int]] = []
    for idx in impact_indices:
        if not shot_groups or idx - shot_groups[-1][-1] > 5:
            shot_groups.append([idx])
        else:
            shot_groups[-1].append(idx)

    results = []
    for shot_idx, impact_group in enumerate(shot_groups):
        # Expand to impact -1 before, +2 after (ball appears slightly after)
        frame_set = set()
        for idx in impact_group:
            for offset in range(-1, 3):
                fi = idx + offset
                if 0 <= fi < len(frames):
                    frame_set.add(fi)

        # Peak-bin extraction: for each frame, find the single strongest
        # bin in the ball velocity band and take the angle at that bin only.
        # This avoids averaging across noisy weak detections.
        peak_angles = []
        peak_snrs = []
        peak_speeds_mph = []
        peak_bins: list[int] = []

        for fi in sorted(frame_set):
            radc_raw = frames[fi].get("radc")
            if radc_raw is None:
                continue
            channels = parse_radc_payload(radc_raw) if isinstance(radc_raw, bytes) else radc_raw

            f1a_iq = to_complex_iq(channels["f1a_i"], channels["f1a_q"])
            f2a_iq = to_complex_iq(channels["f2a_i"], channels["f2a_q"])

            spec = compute_spectrum(f1a_iq, fft_size=fft_size)
            ball_spec = spec[b_lo:b_hi]
            if ball_spec.max() <= 0:
                continue

            # SNR of the peak bin vs full-spectrum noise floor
            full_median = float(np.median(spec[spec > 0]))
            peak_val = float(ball_spec.max())
            snr = peak_val / full_median if full_median > 0 else 0.0
            if snr < 2.0:
                continue

            peak_bin = b_lo + int(np.argmax(ball_spec))

            # Per-bin angle at the peak
            f1a_fft = compute_fft_complex(f1a_iq, fft_size=fft_size)
            f2a_fft = compute_fft_complex(f2a_iq, fft_size=fft_size)
            angles = per_bin_angle_deg(f1a_fft, f2a_fft)

            # Magnitude²-weighted centroid of the per-bin angles across
            # the spectral peak, rather than the raw angle at a single
            # bin. Search a small neighborhood (`centroid_search_bins`)
            # around the peak and include bins whose magnitude is at
            # least `centroid_floor_frac` of the peak. For a range-
            # spread target this integrates the angle estimate across
            # all the energy in the peak; restricting to a neighborhood
            # prevents random noise bins elsewhere in the band (which
            # have similar magnitudes when there is no real ball signal)
            # from contributing. This is the wideband monopulse
            # formulation (Zhang et al., Sensors 2016).
            if centroid_floor_frac < 1.0:
                lo_n = max(b_lo, peak_bin - CENTROID_SEARCH_BINS)
                hi_n = min(b_hi, peak_bin + CENTROID_SEARCH_BINS + 1)
                neigh = spec[lo_n:hi_n]
                neigh_mask = neigh >= peak_val * centroid_floor_frac
                if neigh_mask.any():
                    neigh_indices = np.flatnonzero(neigh_mask) + lo_n
                    neigh_w = neigh[neigh_mask] ** 2
                    neigh_w_sum = float(neigh_w.sum())
                    if neigh_w_sum > 0:
                        centroid_angle = float(
                            np.sum(angles[neigh_indices] * neigh_w)
                            / neigh_w_sum
                        )
                    else:
                        centroid_angle = float(angles[peak_bin])
                else:
                    centroid_angle = float(angles[peak_bin])
            else:
                # Disabled (frac=1.0) — fall back to the legacy
                # single-peak-bin angle for exact backward compatibility.
                centroid_angle = float(angles[peak_bin])

            peak_angles.append(centroid_angle)
            peak_snrs.append(snr)
            peak_bins.append(peak_bin)
            vel = bin_to_velocity_kmh(peak_bin, fft_size, max_speed_kmh)
            peak_speeds_mph.append((200.0 + vel) / 1.609)

        if not peak_angles:
            continue

        angs = np.array(peak_angles)
        snrs = np.array(peak_snrs)
        bins_arr = np.array(peak_bins, dtype=int)

        if len(angs) == 1:
            # Single-frame detection — accept if SNR is strong.
            # Golf balls transit the K-LD7 beam in ~1 frame at 18 FPS,
            # so a single high-SNR frame is the expected case.
            if snrs[0] < 5.0:
                continue
            clean_angs = angs
            clean_snrs = snrs
            clean_bins = bins_arr
        else:
            # Multi-frame: outlier rejection.
            #
            # Drop the frame furthest from the median angle, *unless*
            # one frame's SNR is dramatically larger than the others.
            # In that case the median is being set by low-SNR noise
            # frames around a single high-SNR ball frame, and dropping
            # the angular outlier would discard the only real
            # detection. Instead we drop the lowest-SNR frame.
            clean_mask = np.ones(len(angs), dtype=bool)
            if len(angs) >= 3:
                max_snr = float(snrs.max())
                med_snr = float(np.median(snrs))
                snr_dominant = max_snr > 10.0 * max(med_snr, 1.0)
                if snr_dominant:
                    worst = int(np.argmin(snrs))
                else:
                    med = float(np.median(angs))
                    worst = int(np.argmax(np.abs(angs - med)))
                clean_mask[worst] = False
            clean_angs = angs[clean_mask]
            clean_snrs = snrs[clean_mask]
            clean_bins = bins_arr[clean_mask]

        # SNR²-weighted average of surviving peaks. When the OPS-expected
        # bin is known, frames whose peak bin is far from it (likely
        # clutter latched onto a persistent stripe) get a weight penalty.
        w = clean_snrs ** 2
        if (
            ops_expected_bin is not None
            and ops_bin_outlier_penalty > 1.0
            and ops_bin_outlier_tol >= 0
        ):
            outlier = np.abs(clean_bins - ops_expected_bin) > ops_bin_outlier_tol
            if outlier.any():
                w = w.astype(float).copy()
                w[outlier] = w[outlier] / ops_bin_outlier_penalty
                logger.info(
                    "[RADC] OPS-bin penalty: %d/%d frames > %d bins from "
                    "expected bin %d, weight /%.1f",
                    int(outlier.sum()), len(clean_bins),
                    ops_bin_outlier_tol, ops_expected_bin,
                    ops_bin_outlier_penalty,
                )
        total_w = float(np.sum(w))
        if total_w <= 0:
            continue
        weighted_angle = float(np.sum(clean_angs * w) / total_w)
        corrected_angle = weighted_angle + angle_offset_deg

        # Hard physical bounds — reject obvious outliers before they
        # reach the Shot object. Orientation-aware: vertical [0°, 45°],
        # horizontal [-15°, +15°]. When orientation is None (offline
        # analysis), skip bounds filtering.
        if orientation == "vertical" and not (0.0 <= corrected_angle <= 45.0):
            logger.info(
                "[RADC] Vertical angle %.1f° outside [0, 45] — rejected",
                corrected_angle,
            )
            continue
        if orientation == "horizontal" and abs(corrected_angle) > 15.0:
            logger.info(
                "[RADC] Horizontal angle %.1f° outside ±15° — rejected",
                corrected_angle,
            )
            continue

        avg_speed_mph = float(np.mean(peak_speeds_mph))
        angle_std = float(np.std(clean_angs))
        avg_snr = float(np.mean(clean_snrs))

        # Confidence based primarily on SNR. For RADC, single-frame
        # detection is the expected case (ball transits in ~56ms at 18 FPS),
        # so frame count shouldn't penalize confidence. Multi-frame
        # detections get a bonus from angle consistency.
        frame_count = len(clean_angs)
        snr_score = min(avg_snr / 10.0, 1.0)
        if frame_count == 1:
            # Single frame: confidence driven by SNR alone
            # SNR 5 → 0.50, SNR 10 → 0.75, SNR 15+ → 0.90
            confidence = round(0.40 + snr_score * 0.50, 2)
        else:
            # Multi-frame: SNR + angle consistency bonus
            std_score = max(0.0, 1.0 - angle_std / 15.0)
            confidence = round(snr_score * 0.5 + std_score * 0.3 + min(frame_count / 3.0, 1.0) * 0.2, 2)

        results.append({
            "shot_index": shot_idx,
            "launch_angle_deg": round(corrected_angle, 1),
            "raw_angle_deg": round(weighted_angle, 1),
            "angle_offset_deg": angle_offset_deg,
            "ball_speed_mph": round(avg_speed_mph, 1),
            "confidence": confidence,
            "detection_count": len(peak_angles),
            "frame_count": frame_count,
            "angle_std_deg": round(angle_std, 1),
            "avg_snr_db": round(avg_snr, 1),
            "impact_frames": impact_group,
        })

    return results
