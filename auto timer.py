import cv2
import time
import threading
import numpy as np
from mss import mss
import tkinter as tk
import ctypes
from dataclasses import dataclass

# ====================== 配置区 ======================
# 调试模式：设为 True 会弹出一个名为 "Debug" 的窗口，实时显示摄像头看到的画面和匹配结果
# 调试完毕后请务必改为 False，否则会覆盖游戏窗口
DEBUG_MODE = True 

# 4 个检测区域 (根据你的游戏分辨率调整)
PLAYER_REGIONS = [
    {"top": 600, "left": 200, "width": 100, "height": 100},
    {"top": 700, "left": 200, "width": 100, "height": 100},
    {"top": 800, "left": 200, "width": 100, "height": 100},
    {"top": 900, "left": 200, "width": 100, "height": 100},
]

# 4 个计时器显示位置
TIMER_POSITIONS = [
    {"x": 270, "y": 600},
    {"x": 270, "y": 700},
    {"x": 270, "y": 800},
    {"x": 270, "y": 900},
]

MAX_SECONDS = 91
MATCH_THRESHOLD = 0.85      # 阈值，如果识别不稳定尝试调低到 0.8 或调高到 0.9
HOOK_TEMPLATE_PATH = "hook.webp"
# ====================================================

# 共享状态数据
@dataclass
class PlayerState:
    running: bool = False       # 计时器是否在跑
    start_timestamp: float = 0.0
    current_time: float = 0.0   # 当前显示的时间
    is_pattern_a: bool = True   # 当前是否是图案A（钩子）

# 初始化4个玩家状态
states = [PlayerState() for _ in range(4)]
# 线程锁，防止读写冲突
state_lock = threading.Lock()

def set_click_through(hwnd):
    """设置窗口为点击穿透且背景透明"""
    try:
        # Windows API 常量
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020 # 鼠标穿透的关键
        WS_EX_TOPMOST = 0x00000008
        WS_EX_TOOLWINDOW = 0x00000080  # 不在任务栏显示
        WS_EX_NOACTIVATE = 0x08000000

        user32 = ctypes.windll.user32
        
        # 获取当前样式
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # 叠加样式
        new_style = style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

        # 设置透明色键 (黑色 0x000000 将变为完全透明)
        # 格式是 0x00BBGGRR，这里黑色是0
        LWA_COLORKEY = 0x00000001
        user32.SetLayeredWindowAttributes(hwnd, 0x000000, 0, LWA_COLORKEY)
    except Exception as e:
        print(f"设置穿透属性失败: {e}")

class TimerApp:
    def __init__(self, root):
        self.root = root
        self.root.withdraw() # 隐藏主窗口，我们只用 Toplevel 做悬浮窗

        self.labels = []
        self.windows = []

        for i, pos in enumerate(TIMER_POSITIONS):
            # 创建独立的悬浮窗
            win = tk.Toplevel(root)
            win.title(f"Timer {i}")
            # 去除边框
            win.overrideredirect(True)
            # 置顶
            win.attributes("-topmost", True)
            # 设定几何位置
            win.geometry(f"120x80+{pos['x']}+{pos['y']}")
            # 背景设为黑色（稍后会被过滤为透明）
            win.config(bg="black")
            
            # 关键：设置特定颜色为透明色 (Windows下有效)
            win.attributes("-transparentcolor", "black") 

            # 创建文字标签
            lbl = tk.Label(
                win, 
                text="", 
                font=("Impact", 20), # 字体大一点，粗一点
                fg="#FF3333",        # 红色字体
                bg="black"           # 背景黑
            )
            lbl.pack(expand=True, fill="both")
            
            self.windows.append(win)
            self.labels.append(lbl)

            # 必须等窗口生成后才能设置点击穿透
            # 延迟 2000ms 执行
            win.after(2000, lambda h=win.winfo_id(): set_click_through(h))

        # 启动 GUI 刷新循环
        self.update_gui()

    def update_gui(self):
        with state_lock:
            for i in range(4):
                s = states[i]

                # 如果正在计时，或者时间不为0，显示时间
                if s.running or s.current_time > 0:
                    display_text = f"{int(s.current_time)}"
                    self.labels[i].config(text=display_text)
                else:
                    # 归零状态下不显示，或者显示为空
                    self.labels[i].config(text="")
        
        # 每 2000ms 刷新一次界面 (20 FPS)
        self.root.after(2000, self.update_gui)

def detection_thread_func():
    """后台检测线程"""
    print("检测线程已启动...")
    
    # 加载模板
    template = cv2.imread(HOOK_TEMPLATE_PATH, cv2.IMREAD_GRAYSCALE)
    if template is None:
        print(f"[错误] 找不到图片: {HOOK_TEMPLATE_PATH}，请确保文件在同目录下。")
        return

    sct = mss()
    
    while True:
        
        # 遍历4个玩家区域
        for i in range(4):
            region = PLAYER_REGIONS[i]
            monitor = {
                "top": region["top"], "left": region["left"], 
                "width": region["width"], "height": region["height"]
            }
            
            # 截图并转灰度
            img = np.array(sct.grab(monitor))
            gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)

            # 模板匹配
            match_val = 0.0
            if gray.shape[0] >= template.shape[0] and gray.shape[1] >= template.shape[1]:
                res = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
                min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
                match_val = max_val
            
            # 判定当前是否是 A (钩子)
            is_hook = match_val >= MATCH_THRESHOLD

            # 更新逻辑
            with state_lock:
                s = states[i]
                
                # 状态机逻辑：
                # 1. 从 A (钩子) 变为 其他 (救下) -> 启动计时
                if s.is_pattern_a and not is_hook:
                    s.running = True
                    s.current_time = 0 # 如果需要救下重置为0，解开这行；如果只是继续跑，保持原样
                    s.start_timestamp = time.time()
                    print(f"P{i+1}: 救下 -> 计时开始")
                
                # 2. 从 其他 变为 A (挂钩) -> 重置并停止
                elif not s.is_pattern_a and is_hook:
                    s.running = False
                    s.current_time = 0
                    print(f"P{i+1}: 挂钩 -> 计时重置")

                # 更新当前状态记录
                s.is_pattern_a = is_hook

                # 计时逻辑
                if s.running:
                    # 当前时间 - 刚开始时记录的时间戳 = 实际经过的绝对秒数
                    # 这会完全忽略 sleep 的影响
                    elapsed = time.time() - s.start_timestamp
                    s.current_time = elapsed
                    
                    if s.current_time >= MAX_SECONDS:
                        s.current_time = MAX_SECONDS
                        s.running = False

            # 调试显示
            if DEBUG_MODE:
                # 在图上画框和匹配度
                cv2.putText(img, f"{match_val:.2f}", (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                status_color = (0, 0, 255) if is_hook else (0, 255, 0)
                cv2.rectangle(img, (0,0), (monitor['width']-1, monitor['height']-1), status_color, 2)
                cv2.imshow(f"Debug P{i+1}", img)

        if DEBUG_MODE:
            if cv2.waitKey(1) & 0xFF == 27: # ESC 退出
                break
        
        # 稍微休眠一下减少CPU占用，但不要太久以免漏掉状态
        time.sleep(1) 

if __name__ == "__main__":
    print("程序启动中...")
    
    # 1. 启动检测线程
    t = threading.Thread(target=detection_thread_func, daemon=True)
    t.start()

    # 2. 启动主线程 GUI
    root = tk.Tk()
    app = TimerApp(root)
    
    # 这是一个阻塞调用，直到窗口关闭才会结束
    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("程序退出")