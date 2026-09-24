Estoy poniendo a prueba a Claude Opus 5.5 con un proyecto real: recreamos juntos Vigía, un sistema de videovigilancia con IA, y lo conectamos a cámaras de tránsito en vivo de California.

El punto de partida no fue pedir "una app", sino un objetivo claro: detectar comportamientos sospechosos en video, en tiempo real, 100 % en local, sin reconocimiento facial y en un portátil sin GPU.

Cómo trabajamos:
→ Yo defino el objetivo, las restricciones y qué probar.
→ Opus 5.5 propone la arquitectura, escribe el código y las pruebas, y mide antes y después de cada cambio.
→ Cada trade-off se decide con datos, no por intuición.

Los trade-offs que estamos analizando:

1) Precisión vs CPU. YOLO11s detecta 3 veces más camiones en un cruce de Long Beach, pero usa el doble de CPU, y de noche, en la autopista, no mejoró. Decisión: un modelo por cámara.

2) Sensibilidad vs falsas alarmas. Bajar la confianza de noche multiplicó los autos detectados… y convirtió un cartel verde en "vehículo". Un filtro en capas lo bajó de 94 cuadros en 3 minutos a destellos aislados.

3) Simplicidad vs rendimiento. Un hilo por cámara con el patrón "último cuadro": nunca analiza imágenes atrasadas. El costo apareció al medir: 64 % de CPU con 3 cámaras. Ajustando los hilos de PyTorch/OpenMP bajó a 24 %, sin perder fps.

Siguen abiertos: tracker propio vs ByteTrack/DeepSORT, ONNX/OpenVINO para la CPU y medir precisión y recall con videos etiquetados.

El aprendizaje principal: la cámara real enseña lo que el video de prueba no. Transmisiones que llegan en ráfagas, una cámara que empieza a transmitir una imagen gris, cortes de señal… Varios de esos casos hoy son pruebas automáticas (98 en total).

Sobre el alcance: no entrené un modelo (uso YOLO11 preentrenado), no está en producción y todavía no tengo métricas de precisión. Es el próximo paso.

En el carrusel: el objetivo, la arquitectura y los números detrás de cada decisión.

Stack: Python · YOLO11 · OpenCV · PyTorch · FastAPI · SSE · SQLite · JavaScript
Video: cámaras públicas de Caltrans (datos abiertos).

¿Qué trade-off habrías resuelto distinto?

#ComputerVision #Python #InteligenciaArtificial #ClaudeAI #ArquitecturaDeSoftware
