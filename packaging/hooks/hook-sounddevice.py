"""Linux 音频栈由目标系统提供，不能随冻结解释器一起搬运。

覆盖 PyInstaller 默认 hook：默认会收集 libportaudio 及其 ALSA/JACK 依赖，
旧发行版的库会遮蔽新系统库，使系统 PipeWire/PulseAudio 插件无法加载。
运行时 sounddevice 通过 libportaudio.so.2 使用目标系统的完整音频栈。
"""

binaries = []
datas = []
