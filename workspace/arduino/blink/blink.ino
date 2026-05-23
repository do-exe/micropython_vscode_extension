// PWM smooth fade only on pin 5 (no built-in LED)

const uint8_t PIN_FADE = 5;        // PWM pin on Arduino Uno
const uint16_t STEP_DELAY_MS = 8;  // smaller = smoother but faster; 8..20 recommended
const uint16_t MAX_PWM = 255;

void fadeOnce(uint8_t pin) {
  // Fade up
  for (uint16_t v = 0; v <= MAX_PWM; v++) {
    analogWrite(pin, v);
    delay(STEP_DELAY_MS);
  }
  // Fade down
  for (int v = (int)MAX_PWM; v >= 0; v--) {
    analogWrite(pin, (uint16_t)v);
    delay(STEP_DELAY_MS);
  }
}

void setup() {
  pinMode(PIN_FADE, OUTPUT);
  analogWrite(PIN_FADE, 0);
}

void loop() {
  fadeOnce(PIN_FADE);
}

