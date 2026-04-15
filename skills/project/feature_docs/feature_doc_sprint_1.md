# 🧠 Cricket Hot Match Engine — Feature Specification (sprint 1)

---

## 1. 🎯 Objective

Design and implement a **deterministic, real-time engine** that processes ball-by-ball cricket data for a T20 chase and produces:

* Win probability (per ball)
* Hotness score (per ball)
* Hotness forecast (next 6 balls)
* Notification signals (pre-match + in-game)

The system must prioritize **recall over precision**, ensuring minimal missed high-drama matches.

---

## 2. 🧩 Scope

### Included

* 2nd innings (chase) processing only
* Ball-by-ball ingestion
* Deterministic state updates
* Model inference (win probability + forecast)
* Signal generation

### Excluded

* UI / extension logic
* Notification delivery mechanisms
* Multi-match orchestration
* First innings modeling beyond initialization

---

## 3. 🏗️ System Architecture

```
BallEvent
   ↓
ChaseState Builder
   ↓
FeatureExtractor
   ↓
WinProbModel (NN)
   ↓
HotnessCalculator
   ↓
HotnessForecaster (NN)
   ↓
SignalEvaluator
   ↓
EngineOutput
```

---

## 4. 📦 Core Data Structures

---

### 4.1 BallEvent

Represents a single legal delivery.

```python
class BallEvent:
    match_id: str
    innings: int
    over: float
    runs: int
    extras: int
    wicket: bool
    timestamp: datetime
```

---

### 4.2 ChaseState

Represents current state of the chase.

```python
class ChaseState:
    match_id: str
    target: int
    total_balls: int

    runs_scored: int
    wickets: int
    balls_faced: int

    runs_needed: int
    balls_remaining: int
```

---

### 4.3 HotnessState

Maintains temporal memory.

```python
class HotnessState:
    win_prob_history: deque(maxlen=12)
    hotness_history: deque(maxlen=12)
```

---

## 5. ⚙️ Functional Components

---

### 5.1 StateUpdater

Responsible for updating chase state per ball.

#### Requirements (Gherkin)

```
Feature: State Update

Scenario: Valid ball event updates state correctly
  Given a valid BallEvent
  When applied to the current ChaseState
  Then runs_scored must increase by event.runs + event.extras
  And balls_faced must increment by 1
  And wickets must increment if event.wicket is true
  And runs_needed must be updated as target - runs_scored
  And balls_remaining must be updated as total_balls - balls_faced
```

```
Scenario: Balls remaining cannot be negative
  Given balls_faced exceeds total_balls
  When state is updated
  Then balls_remaining must be clamped to 0
```

---

### 5.2 FeatureExtractor

Extracts model input features.

#### Features

* runs_needed
* balls_remaining
* wickets
* rrr
* balls_fraction
* wickets_fraction

#### Requirements

```
Feature: Feature Extraction

Scenario: Extract features correctly
  Given a valid ChaseState
  When features are extracted
  Then output must contain exactly 6 features
  And rrr must be runs_needed / max(balls_remaining, 1)
  And balls_fraction must be balls_remaining / total_balls
  And wickets_fraction must be wickets / 10
```

```
Scenario: Division safety
  Given balls_remaining is 0
  When computing rrr
  Then denominator must be treated as 1
```

---

### 5.3 WinProbModel

Neural network inference layer.

#### Requirements

```
Feature: Win Probability Prediction

Scenario: Predict win probability
  Given normalized feature vector
  When passed to WinProbModel
  Then output must be a float between 0 and 1
```

```
Scenario: Deterministic output
  Given identical feature inputs
  When prediction is run multiple times
  Then output must be identical
```

```
Scenario: Correct normalization
  Given raw features
  When passed to model
  Then they must be normalized using stored mean and std
```

---

### 5.4 HotnessCalculator

Computes hotness using win probability.

#### Formula

```
closeness = 1 - 2 * abs(win_prob - 0.5)
momentum  = abs(win_prob - win_prob[t-6])

hotness = clip(0, 1, closeness * 0.6 + momentum * 5 * 0.4)
```

#### Requirements

```
Feature: Hotness Calculation

Scenario: Compute closeness correctly
  Given win_prob = 0.5
  Then closeness must be 1
```

```
Scenario: Compute momentum correctly
  Given at least 6 previous win_prob values
  When computing momentum
  Then it must be absolute difference between current and value 6 balls ago
```

```
Scenario: Insufficient history
  Given fewer than 6 previous values
  When computing momentum
  Then momentum must be 0
```

```
Scenario: Hotness bounds
  Given computed hotness
  Then it must be clipped between 0 and 1
```

---

### 5.5 HotnessForecaster

Predicts future hotness.

#### Input

* Last 12 hotness values
* balls_remaining_fraction

#### Output

* max hotness in next 6 balls

#### Requirements

```
Feature: Hotness Forecasting

Scenario: Valid forecast input
  Given at least 12 hotness values
  And balls_remaining_fraction
  When passed to forecaster
  Then output must be a float between 0 and 1
```

```
Scenario: Insufficient history
  Given fewer than 12 hotness values
  When forecast is requested
  Then forecast must not be computed
```

```
Scenario: Deterministic forecast
  Given identical input vectors
  When prediction is run multiple times
  Then output must be identical
```

---

### 5.6 Forecast Gating

Forecast should only be evaluated after midpoint.

#### Requirements

```
Feature: Forecast Gating

Scenario: Before 60 balls
  Given balls_faced < 60
  When forecast is evaluated
  Then forecast must not be generated
```

```
Scenario: After 60 balls
  Given balls_faced >= 60
  And sufficient history exists
  When forecast is evaluated
  Then forecast must be generated
```

---

### 5.7 SignalEvaluator

Generates user-facing signals.

---

#### A. Pre-Match Signal

```
Feature: Pre-Match Signal

Scenario: Balanced chase
  Given ball number is 1
  And win_prob is between 0.40 and 0.60
  When evaluated
  Then signal "50/50 chase" must be triggered
```

---

#### B. Forecast Signal

```
Feature: Forecast Signal

Scenario: Match heating up
  Given balls_faced >= 60
  And forecast >= threshold
  When evaluated
  Then signal "match heating up" must be triggered
```

```
Scenario: Below threshold
  Given forecast < threshold
  When evaluated
  Then no signal must be triggered
```

---

### 5.8 EngineOrchestrator

Coordinates full pipeline.

#### Requirements

```
Feature: Engine Orchestration

Scenario: Full pipeline execution
  Given a new BallEvent
  When processed
  Then state must be updated
  And features must be extracted
  And win probability must be computed
  And hotness must be computed
  And forecast must be computed if eligible
  And signals must be evaluated
  And output must be emitted
```

```
Scenario: Order of operations
  Given a ball event
  Then execution order must be:
    1. State update
    2. Feature extraction
    3. Win probability
    4. Hotness
    5. Forecast
    6. Signal evaluation
```

---

## 6. 📤 EngineOutput

```python
class EngineOutput:
    match_id: str

    win_prob: float
    hotness: float
    forecast: float | None

    runs_needed: int
    balls_remaining: int
    wickets: int

    signals: list[str]
```

---

## 7. ⚠️ Non-Functional Requirements

---

### Determinism

```
Feature: Determinism

Scenario: Same input produces same output
  Given identical sequence of BallEvents
  When engine is replayed
  Then outputs must be identical
```

---

### Replayability

```
Feature: Replay

Scenario: Full match replay
  Given stored ball events
  When replayed sequentially
  Then final state and outputs must match live execution
```

---

### Latency

```
Feature: Performance

Scenario: Real-time processing
  Given a new BallEvent
  When processed
  Then total processing time must be < 100ms
```

---

### Idempotency

```
Feature: Idempotency

Scenario: Duplicate ball event
  Given an already processed BallEvent
  When received again
  Then it must not alter state or outputs
```

---

## 8. 🧠 Known Constraints

* Model trained on IPL data only
* Requires correct total_balls inference
* Assumes clean ordering of ball events
* Forecast model slightly regresses toward mean (acceptable)

---

## 9. 🚀 MVP Definition

System is considered MVP-complete when:

* Processes live ball-by-ball data
* Produces win_prob, hotness, forecast per ball
* Triggers:

  * pre-match signal
  * forecast signal
* Maintains deterministic and replayable behavior
