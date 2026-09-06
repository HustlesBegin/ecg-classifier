from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np


BASE_DIR = Path(__file__).resolve().parent
RUNTIME_TMP = BASE_DIR / ".runtime_tmp"
tempfile.tempdir = str(RUNTIME_TMP)


TARGET_FS = 360
DEFAULT_INPUT_FS = 360
FEATURE_COUNT = 14


@dataclass(frozen=True)
class ECGClassification:
    bpm: float | None
    model_class: int | None
    model_label: str
    rate_label: str
    confidence: float | None
    beats_detected: int
    message: str


class ECGPipeline:
    def __init__(self, base_dir: Path | str = BASE_DIR) -> None:
        import joblib

        self.base_dir = Path(base_dir)
        self.model = joblib.load(self.base_dir / "ecg_rf_model.pkl")
        self.scaler = joblib.load(self.base_dir / "ecg_scaler.pkl")
        self.feature_names = np.load(
            self.base_dir / "ecg_feature_names.npy", allow_pickle=True
        )
        if len(self.feature_names) != FEATURE_COUNT:
            raise ValueError(f"Se esperaban {FEATURE_COUNT} features, llegaron {len(self.feature_names)}")

    def classify_segment(self, raw_ecg: list[float] | np.ndarray, fs: int = DEFAULT_INPUT_FS) -> ECGClassification:
        ecg = np.asarray(raw_ecg, dtype=float)
        if ecg.size < fs * 3:
            return self._empty("Esperando mas senal")

        if np.std(ecg) < 1e-8:
            return self._empty("Senal plana o insuficiente")

        if fs != TARGET_FS:
            ecg = resample_signal(ecg, fs, TARGET_FS)
            fs_eff = TARGET_FS
        else:
            fs_eff = fs

        try:
            ecg_filt = bandpass_filter(ecg, fs_eff)
        except ValueError as exc:
            return self._empty(f"No se pudo filtrar la senal: {exc}")

        beats, rr_prev_list = build_beats_and_rr_from_signal(ecg_filt, fs_eff)
        valid_rr = rr_prev_list[rr_prev_list > 0]
        if len(beats) == 0 or len(valid_rr) == 0:
            return self._empty("Calculando picos R")

        feats = []
        for beat, rr_prev in zip(beats, rr_prev_list):
            beat_norm = normalize_beat(beat)
            feats.append(extract_features_from_beat(beat_norm, fs_eff, rr_prev=rr_prev))

        feats_arr = np.asarray(feats, dtype=float)
        if feats_arr.ndim != 2 or feats_arr.shape[1] != FEATURE_COUNT:
            return self._empty("Vector de features invalido")

        feats_scaled = self.scaler.transform(feats_arr)
        preds = self.model.predict(feats_scaled)
        pred = int(preds[-1])
        bpm = float(60.0 / valid_rr[-1])
        confidence = None
        if hasattr(self.model, "predict_proba"):
            proba = self.model.predict_proba(feats_scaled[-1:])[0]
            confidence = float(np.max(proba))

        return ECGClassification(
            bpm=bpm,
            model_class=pred,
            model_label="Normal" if pred == 0 else "Anormal",
            rate_label=rate_label_from_bpm(bpm),
            confidence=confidence,
            beats_detected=int(len(beats)),
            message="OK",
        )

    @staticmethod
    def _empty(message: str) -> ECGClassification:
        return ECGClassification(
            bpm=None,
            model_class=None,
            model_label="Calculando",
            rate_label="Calculando",
            confidence=None,
            beats_detected=0,
            message=message,
        )


def rate_label_from_bpm(bpm: float | None) -> str:
    if bpm is None or bpm <= 0:
        return "Calculando"
    if bpm > 100:
        return "Acelerado"
    if bpm < 60:
        return "Bajo"
    return "Normal"


def resample_signal(sig: np.ndarray, fs_in: int, fs_target: int = TARGET_FS) -> np.ndarray:
    from scipy.signal import resample

    if fs_in == fs_target:
        return sig
    duration = len(sig) / fs_in
    new_length = max(1, int(duration * fs_target))
    return resample(sig, new_length)


def bandpass_filter(x: np.ndarray, fs: int, low: float = 0.5, high: float = 40.0, order: int = 4) -> np.ndarray:
    from scipy.signal import butter, filtfilt

    b, a = butter(order, [low / (fs / 2), high / (fs / 2)], btype="band")
    return filtfilt(b, a, x)


def normalize_beat(beat: np.ndarray) -> np.ndarray:
    beat = beat - np.mean(beat)
    std = np.std(beat)
    if std == 0:
        return beat
    return beat / std


def detect_r_peaks(ecg: np.ndarray, fs: int, distance_sec: float = 0.25, height_factor: float = 0.5) -> np.ndarray:
    from scipy.signal import find_peaks

    ecg_norm = ecg - np.mean(ecg)
    ecg_norm = ecg_norm / (np.std(ecg_norm) + 1e-8)
    distance = int(distance_sec * fs)
    height = height_factor * np.std(ecg_norm)
    peaks, _ = find_peaks(ecg_norm, distance=distance, height=height)
    return peaks


def build_beats_and_rr_from_signal(
    ecg: np.ndarray,
    fs: int,
    win_ms_before: int = 200,
    win_ms_after: int = 400,
) -> tuple[np.ndarray, np.ndarray]:
    r_peaks = detect_r_peaks(ecg, fs)
    rr_intervals = np.diff(r_peaks) / fs
    win_before = int(win_ms_before * 1e-3 * fs)
    win_after = int(win_ms_after * 1e-3 * fs)
    win_len = win_before + win_after

    beats = []
    rr_prev_list = []
    for idx, sample_idx in enumerate(r_peaks):
        start = sample_idx - win_before
        end = sample_idx + win_after
        if start < 0 or end > len(ecg):
            continue
        segment = ecg[start:end]
        if len(segment) != win_len:
            continue
        beats.append(segment)
        rr_prev_list.append(-1.0 if idx == 0 else rr_intervals[idx - 1])

    return np.asarray(beats), np.asarray(rr_prev_list)


def extract_features_from_beat(beat: np.ndarray, fs: int, rr_prev: float | None = None) -> np.ndarray:
    from scipy.fft import rfft, rfftfreq

    feat = []

    mean_val = np.mean(beat)
    std_val = np.std(beat)
    rms = np.sqrt(np.mean(beat ** 2))
    energy = np.sum(beat ** 2)
    max_val = np.max(beat)
    min_val = np.min(beat)
    peak_to_peak = max_val - min_val
    feat.extend([mean_val, std_val, rms, energy, max_val, min_val, peak_to_peak])

    beat_abs = np.abs(beat)
    max_abs = np.max(beat_abs)
    if max_abs > 0:
        threshold = 0.5 * max_abs
        center = len(beat) // 2
        left = center
        while left > 0 and beat_abs[left] > threshold:
            left -= 1
        right = center
        while right < len(beat_abs) - 1 and beat_abs[right] > threshold:
            right += 1
        qrs_duration = (right - left) / fs
    else:
        qrs_duration = 0.0
    feat.append(qrs_duration)

    if rr_prev is None or rr_prev <= 0:
        rr_val = -1.0
        bpm = -1.0
    else:
        rr_val = rr_prev
        bpm = 60.0 / rr_prev
    feat.extend([rr_val, bpm])

    n = len(beat)
    yf = np.abs(rfft(beat))
    xf = rfftfreq(n, 1 / fs)
    f_dom = xf[np.argmax(yf)] if np.any(yf > 0) else 0.0
    low_band = (xf >= 0.5) & (xf <= 15)
    mid_band = (xf > 15) & (xf <= 40)
    e_low = np.sum(yf[low_band] ** 2)
    e_mid = np.sum(yf[mid_band] ** 2)
    spectral_centroid = np.sum(xf * yf) / np.sum(yf) if np.sum(yf) > 0 else 0.0
    feat.extend([f_dom, e_low, e_mid, spectral_centroid])

    return np.asarray(feat)
