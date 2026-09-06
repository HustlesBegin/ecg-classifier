# ECG Acquisition and Heartbeat Classification with a PIC Microcontroller

## Project Overview

This academic project combines embedded electronics, digital signal processing, and machine learning to acquire an electrocardiogram (ECG), filter the signal, classify its heartbeats using a Random Forest model, and display the ECG waveform and classification results on a local web page.

The purpose is to connect the complete acquisition and analysis process: from a physical ECG signal captured through a sensor and digitized by a PIC microcontroller, to heartbeat analysis and visualization on a computer. It demonstrates how a microcontroller-based acquisition system can work together with a Python application to process physiological signals.

## System Workflow

```text
ECG signal
    |
    v
AD8232 ECG module
    |
    v
PIC16F877A microcontroller: sampling and analog-to-digital conversion
    |
    v
UART transmission through a CP2102 USB-to-serial adapter
    |
    v
Python application
    |-- Raw ECG waveform ----------------------------------|
    |                                                      |
    v                                                      |
Band-pass filtering                                        |
    |                                                      |
    v                                                      |
R-peak detection and heartbeat segmentation                |
    |                                                      |
    v                                                      |
Feature extraction and scaling                             |
    |                                                      |
    v                                                      |
Random Forest classification and heart rate estimation     |
    |                                                      |
    v                                                      v
Local web page: waveform, heartbeat classification, and BPM
```

## ECG Signal Acquisition

The acquisition stage uses an **AD8232 ECG module** and a **PIC16F877A microcontroller**. The AD8232 provides the analog ECG output, which the PIC reads through its analog-to-digital converter. The firmware also monitors the module's lead-off signals to report whether the electrodes are connected.

The firmware is written in C for the MPLAB XC8 compiler and is configured for a 20 MHz crystal. Timer-based sampling targets **360 samples per second**, matching the sampling frequency used by the processing pipeline. The PIC sends each sample to the computer over UART at **115200 baud**, using a CP2102 USB-to-serial adapter.

Each transmitted sample contains a timestamp, the raw ADC reading, the calculated voltage, and the electrode connection status:

```text
time_ms,ecg_raw,ecg_volt,lead_ok
1000,512,2.5024,1
```

The acquisition firmware is included in `PIC/main.c`.

## Signal Processing

The Python application receives the serial stream and maintains a buffer containing up to ten seconds of ECG data. Before classification, the signal passes through the following stages:

1. **Filtering:** A fourth-order Butterworth band-pass filter from 0.5 to 40 Hz reduces slow baseline variations and high-frequency noise.
2. **R-peak detection:** Peaks in the filtered signal are detected to locate candidate heartbeats and calculate the intervals between consecutive peaks.
3. **Heartbeat segmentation:** A window extending 200 ms before and 400 ms after each detected peak is extracted when enough surrounding samples are available.
4. **Normalization:** Each heartbeat is centered and normalized by its standard deviation.
5. **Feature extraction:** Fourteen numerical features describe the heartbeat's amplitude, energy, timing, and frequency content.
6. **Feature scaling:** The saved scaler transforms the features before they are passed to the trained classifier.

The extracted features include mean, standard deviation, RMS, energy, maximum and minimum amplitude, peak-to-peak amplitude, an approximate QRS duration, the previous RR interval, BPM, dominant frequency, energy in two frequency bands, and spectral centroid.

## Random Forest Heartbeat Classification

The training notebook, `proyecto.ipynb`, uses annotated ECG records from the **MIT-BIH Arrhythmia Database**, accessed through `wfdb`. It extracts heartbeat features, splits the data into training and test sets, applies a standard scaler, and trains a Random Forest classifier with 200 trees.

The notebook uses a binary label mapping:

- **Class 0 — Normal:** Annotations labeled `N`.
- **Class 1 — Abnormal:** All other annotation symbols included by the notebook's mapping.

After training, the notebook generates and saves the following artifacts in the project root:

- `ecg_rf_model.pkl`: the trained Random Forest classifier.
- `ecg_scaler.pkl`: the scaler fitted to the training features.
- `ecg_feature_names.npy`: the names and expected order of the 14 input features.

These generated files are not required in the source repository. A user can create them by running the notebook in order through the **Save the trained pipeline** section. They must exist in the same directory as `app.py` before starting ECG acquisition, because the local application loads them to process and classify incoming heartbeats. If they have not been generated, the web interface can start, but model-based acquisition and classification cannot begin.

During acquisition, the application processes the buffered signal approximately once per second and reports the prediction for the latest complete detected heartbeat. Heart rate is estimated from an RR interval and displayed separately from the model's classification. An abnormal model prediction does not necessarily mean an elevated heart rate or identify a specific arrhythmia.

## Local Web Interface

A **FastAPI** server connects the acquisition and processing stages to a browser interface built with HTML, CSS, and JavaScript. **WebSocket** messages deliver new samples and analysis results to the page.

The interface allows the user to select a serial port, start or stop acquisition, and monitor:

- The incoming raw ECG waveform.
- The serial connection and electrode connection status.
- The estimated heart rate in beats per minute.
- The latest heartbeat classification.
- A separate heart rate category based on the calculated BPM.

The displayed waveform uses the raw samples; filtering is applied within the classification pipeline. Classification pauses when the incoming electrode status indicates disconnection.

The server runs locally at `http://127.0.0.1:8000` on the computer connected to the acquisition hardware.

## Project Structure

| File or directory | Purpose |
| --- | --- |
| `PIC/main.c` | PIC16F877A firmware for ECG sampling and serial transmission. |
| `app.py` | FastAPI server, serial acquisition, buffering, and WebSocket communication. |
| `ecg_pipeline.py` | Filtering, peak detection, feature extraction, and model inference. |
| `proyecto.ipynb` | Model training, evaluation, feature analysis, and signal-processing examples. |
| `ecg_rf_model.pkl` | Random Forest classifier generated by the training notebook. |
| `ecg_scaler.pkl` | Feature scaler generated by the training notebook. |
| `ecg_feature_names.npy` | Feature names and order generated by the training notebook. |
| `index.html` | Local monitoring page. |
| `static/app.js` | Live waveform rendering and interface communication. |
| `static/style.css` | Interface styling. |
| `requirements.txt` | Python dependencies for the local application. |

## Running the Local Application

The application requires Python 3.10 or later. If the generated model files are not already present, first install Jupyter and the notebook dependencies:

```powershell
python -m pip install jupyterlab matplotlib wfdb numpy scipy scikit-learn joblib
python -m jupyterlab
```

Open `proyecto.ipynb` and run its cells in order through the model export section. This creates `ecg_rf_model.pkl`, `ecg_scaler.pkl`, and `ecg_feature_names.npy` in the project root.

Once those three files are in the same directory as `app.py`, run the application from the project folder on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open [the local monitor](http://127.0.0.1:8000), connect the acquisition hardware, refresh the serial port list, select the appropriate port, and start acquisition. The pipeline needs at least three seconds of signal and enough detectable heartbeats before it can produce a result.

## Project Scope

This project is an educational prototype for integrating ECG acquisition, signal processing, machine learning, and local visualization. Its classification output is intended for experimentation and demonstration, not medical diagnosis. The notebook evaluates a random split of heartbeats; it does not establish performance on independent patients or clinical validation of the physical acquisition system.
