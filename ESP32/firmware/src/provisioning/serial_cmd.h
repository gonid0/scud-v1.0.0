#pragma once

// Неблокирующий обработчик provisioning-команд через Serial.
// Вызывается в loop() или provisioning-loop.
void handle_provisioning_serial();
