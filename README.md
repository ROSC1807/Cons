# keyboard_count

一个面向 macOS 的键盘 / 鼠标活动计数器。

它只统计数量，不记录具体按键内容，也不记录鼠标坐标。项目支持两种使用方式：
- 命令行后台采集
- Tkinter 桌面仪表盘可视化

## 功能特性

- 全局键盘按键次数统计
- 全局鼠标点击次数统计
- 鼠标左键 / 右键 / 中键分类统计
- 累计统计与本次会话统计
- 最近 24 小时分钟级历史趋势
- 实时桌面仪表盘
- JSON 持久化保存

## 环境要求

- macOS
- Python 3.11+
- `pynput`
- GUI 模式需要 `tkinter`

### 当前环境注意事项

如果你的默认 `python3` 没有 `tkinter`，GUI 模式会启动失败。

在这台机器上，推荐使用下面这个解释器来运行 GUI：

```bash
/opt/homebrew/bin/python3.11
```

## 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

如果你要运行 GUI，并且默认 `python3` 没有 Tk 支持，请改用：

```bash
/opt/homebrew/bin/python3.11 -m pip install -r requirements.txt
```

## 运行方式

### 1. 无界面运行

```bash
python3 key_mouse_counter.py
```

### 2. 周期打印 JSON 快照

```bash
python3 key_mouse_counter.py --print-interval 5
```

### 3. 指定输出文件

```bash
python3 key_mouse_counter.py --file ./my_counts.json
```

### 4. 启动 GUI 仪表盘

推荐：

```bash
/opt/homebrew/bin/python3.11 key_mouse_counter.py --gui
```

如果你的默认 `python3` 已经自带 Tkinter，也可以直接运行：

```bash
python3 key_mouse_counter.py --gui
```

### 5. 调整保存频率

```bash
python3 key_mouse_counter.py --flush-interval 2
```

## macOS 权限说明

这个项目依赖全局输入监听。首次运行前，你需要给“启动 Python 的应用”授予权限，而不是只给脚本本身授权。

通常需要在下面位置开启权限：

- System Settings
- Privacy & Security
- Accessibility

有些环境下还需要打开：

- Input Monitoring

常见宿主应用包括：
- Terminal
- iTerm
- VS Code

如果授予权限后仍然没有计数，建议：
- 关闭并重新打开 Terminal / iTerm / VS Code
- 再重新启动脚本

## 数据文件说明

默认输出文件是：

```bash
input_counts.json
```

文件里会保存：
- 累计键盘次数
- 累计鼠标次数
- 左 / 右 / 中键统计
- 本次会话统计
- 最近 24 小时分钟级历史
- 相关 UTC 时间戳

当前数据结构版本为 `schema_version = 2`。

## GUI 仪表盘说明

当前桌面仪表盘包含：

- 顶部状态区
  - 运行状态
  - 最近活动时间
  - 会话时长

- 四张概览卡片
  - 累计键盘
  - 累计鼠标
  - 本次键盘
  - 本次鼠标

- 两张趋势图
  - 最近 24 小时键盘趋势
  - 最近 24 小时鼠标趋势

- 一张点击分布图
  - 左键 / 右键 / 中键占比

## 运行测试

```bash
python3 -m unittest tests/test_counter.py
```

如果你想和 GUI 运行环境保持一致，建议用：

```bash
/opt/homebrew/bin/python3.11 -m unittest tests/test_counter.py
```

## 已知限制

- 当前主要面向 macOS 使用
- GUI 依赖 Tkinter
- GUI 目前是只读展示，不支持交互筛选
- 不记录具体按键内容，只记录计数
- 24 小时历史是分钟级聚合，不保存逐事件明细
