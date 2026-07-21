"""E4: ML Response Quality Predictor — logistic regression with online learning.

Predicts P(👍) for each response BEFORE sending. Low-quality predictions
trigger automatic retry or strategy switch. Pure NumPy, <10ms inference.

Features (7-dim):
  [msg_len_norm, hour_sin, hour_cos, personality_id, emotion_id, topic_id, resp_len_norm]

Training: Online SGD with L2 regularization. Updates on every feedback event.
"""
import math
import json
import hashlib
from pathlib import Path
from typing import Optional, Tuple
import numpy as np

# Feature indices
F_MSG_LEN = 0
F_HOUR_SIN = 1
F_HOUR_COS = 2
F_PERSONALITY = 3
F_EMOTION = 4
F_TOPIC = 5
F_RESP_LEN = 6
N_FEATURES = 7

# Personality encoding
PERSONALITY_MAP = {"sassy": 0, "analyst": 1, "gentle": 2}
# Emotion encoding (from message analyzer)
EMOTION_MAP = {"anxiety": 0, "sadness": 1, "anger": 2, "confusion": 3,
               "heartbreak": 4, "neutral": 5, "joy": 6}
# Topic encoding (from preference DAO)
TOPIC_MAP = {"wealth": 0, "love": 1, "career": 2, "health": 3, "growth": 4, "general": 5}


class QualityPredictor:
    """Online logistic regression for response quality prediction.

    Weights updated via SGD after each feedback event (👍/👎).
    Model saved to disk for persistence across restarts.
    """

    def __init__(self, model_path: str = "/opt/fortune-agent/models/quality_model.npz",
                 learning_rate: float = 0.01, l2_lambda: float = 0.001):
        self.model_path = Path(model_path)
        self.lr = learning_rate
        self.l2 = l2_lambda  # L2 regularization strength

        # Initialize or load weights
        if self.model_path.exists():
            self._load()
        else:
            # Xavier-like init for small network
            rng = np.random.RandomState(42)
            self.weights = rng.randn(N_FEATURES) * 0.01
            self.bias = 0.0
            self.n_updates = 0

    def _features(self, message: str, hour: int, personality: str,
                  emotion: str, topic: str, response_len: int) -> np.ndarray:
        """Extract normalized feature vector from request context."""
        x = np.zeros(N_FEATURES, dtype=np.float32)

        # Message length (log-normalized, cap at 500)
        x[F_MSG_LEN] = min(math.log(max(len(message), 1)), math.log(500)) / math.log(500)

        # Hour (cyclic encoding — time of day matters for user mood)
        hour_rad = hour * 2 * math.pi / 24
        x[F_HOUR_SIN] = math.sin(hour_rad)
        x[F_HOUR_COS] = math.cos(hour_rad)

        # Personality
        x[F_PERSONALITY] = PERSONALITY_MAP.get(personality, 1) / 2.0

        # Emotion
        x[F_EMOTION] = EMOTION_MAP.get(emotion, 5) / 6.0

        # Topic
        x[F_TOPIC] = TOPIC_MAP.get(topic, 5) / 5.0

        # Response length (log-normalized)
        x[F_RESP_LEN] = min(math.log(max(response_len, 1)), math.log(3000)) / math.log(3000)

        return x

    def predict(self, message: str, hour: int = None, personality: str = "sassy",
                emotion: str = "neutral", topic: str = "general",
                response_len: int = 500) -> float:
        """Predict P(👍) for a response. Returns probability in [0, 1]."""
        import datetime
        if hour is None:
            hour = datetime.datetime.now().hour
        x = self._features(message, hour, personality, emotion, topic, response_len)
        # Logistic function: P(y=1|x) = 1 / (1 + exp(-wx - b))
        logit = np.dot(self.weights, x) + self.bias
        # Clip to avoid overflow
        logit = np.clip(logit, -10, 10)
        return 1.0 / (1.0 + math.exp(-logit))

    def update(self, message: str, hour: int, personality: str,
               emotion: str, topic: str, response_len: int,
               was_positive: bool):
        """Online SGD update after receiving feedback (👍=1, 👎=0)."""
        x = self._features(message, hour, personality, emotion, topic, response_len)
        y = 1.0 if was_positive else 0.0
        y_pred = self.predict(message, hour, personality, emotion, topic, response_len)

        # Gradient of cross-entropy loss with L2 regularization
        error = y_pred - y
        grad_w = error * x + self.l2 * self.weights
        grad_b = error

        # SGD update
        self.weights -= self.lr * grad_w
        self.bias -= self.lr * grad_b
        self.n_updates += 1

        # Decay learning rate over time
        if self.n_updates % 100 == 0:
            self.lr *= 0.95

        # Periodic save
        if self.n_updates % 50 == 0:
            self._save()

    def should_retry(self, message: str, personality: str = "sassy",
                     emotion: str = "neutral", topic: str = "general",
                     response_len: int = 500, threshold: float = 0.3) -> bool:
        """Check if response quality is predicted to be too low. Triggers retry."""
        prob = self.predict(message, personality=personality, emotion=emotion,
                           topic=topic, response_len=response_len)
        return prob < threshold

    def _save(self):
        """Persist model weights to disk."""
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(str(self.model_path),
                 weights=self.weights, bias=self.bias,
                 n_updates=self.n_updates, lr=self.lr)

    def _load(self):
        """Load model weights from disk."""
        data = np.load(str(self.model_path))
        self.weights = data["weights"]
        self.bias = float(data["bias"])
        self.n_updates = int(data["n_updates"])
        self.lr = float(data["lr"])
        print(f"Loaded quality model: {self.n_updates} updates, lr={self.lr:.4f}")

    @property
    def confidence(self) -> float:
        """Model confidence based on number of training samples."""
        return min(1.0, self.n_updates / 100)  # Max confidence after 100 updates
