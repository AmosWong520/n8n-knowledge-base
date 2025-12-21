import json
import os
import shutil
import re
import time
from pathlib import Path

# ================= 🚀 配置区域 (请修改这里) =================

# 1. 基础文件路径
ROOT = Path(".")
SRC_FILE = ROOT / "raw" / "n8n_nodes_library.json"
OUTPUT_DIR = ROOT / "output"

# 2. 硅基流动 (SiliconFlow) / DeepSeek 配置
ENABLE_AI = True  # 开关：设置为 False 则只做基础清洗，不生成 Manifest
API_KEY = "sk-plppwsqhdtwwwamcdugzwofxizixyiemvoicnyjjndhkuuwt"  # 🔴 这里填入你的 SiliconFlow API Key
API_BASE = "[https://api.siliconflow.cn/v1](https://api.siliconflow.cn/v1)"       # 硅基流动官方地址
MODEL_NAME = "Pro/deepseek-ai/DeepSeek-V3.1-Terminus"           # 指定 DeepSeek-V3 模型

# ==========================================================

# 尝试导入 OpenAI SDK
try:
    from openai import OpenAI
except ImportError:
    print("❌ 错误: 未找到 openai 库。请运行: pip install openai")
    ENABLE_AI = False

# 初始化 AI 客户端
client = None
if ENABLE_AI:
    try:
        client = OpenAI(api_key=API_KEY, base_url=API_BASE)
        print(f"✅ AI Client initialized with model: {MODEL_NAME}")
    except Exception as e:
        print(f"❌ AI Client init failed: {e}")
        ENABLE_AI = False

# ================= 🧠 IDS 2.0 系统提示词 =================
IDS_SYSTEM_PROMPT = """
# Role
你是一位精通 n8n 架构与自动化工作流设计的系统架构师。你正在构建一套基于 "IDS 2.0 (Interoperability Design Specification)" 的自动化生成系统。

# Task
我会给你一个 n8n 节点的清洗后的 JSON 定义。你需要分析该节点的功能、参数和语义，返回且仅返回一个包含 `ids_manifest` 字段的 JSON 对象。

# IDS 2.0 Manifest 规则定义
`ids_manifest` 必须包含以下逻辑判断：

1. **role (角色判定):**
   - webhook/schedule/cron/trigger -> "trigger"
   - AI/LLM/LangChain/Agent -> "processor"
   - Database/Write/File Save/Google Sheets Write -> "sink"
   - IF/Switch/Merge/Filter -> "logic"
   - HTTP Request/API/Send Email -> "action"
   - Read File/Google Sheets Read -> "source"

2. **input_contract (输入契约):**
   - **preferred_source:** 该节点的数据输入参数默认应引用上游 DBP 协议的哪个路径？
   - 规则：绝大多数业务节点应引用 `{{ $json.payload.primary }}` (Golden Path)。
   - 仅特定的元数据处理节点才引用 `{{ $json.metadata }}`。
   - 如果节点支持二进制处理（如上传文件），请设置 `accepts_binary: true`。

3. **output_contract (输出契约):**
   - **standardizer_logic:** 编写一段 JavaScript 伪代码，描述如何将该节点的原始输出（Raw Output）清洗为 IDS 标准信封（StandardEnvelope）。
   - 模板: "return { payload: { primary: upstream.xxx } }"
   - 如果节点输出文本，逻辑通常为 `upstream.text` 或 `upstream.content`。
   - 如果节点输出整个 JSON 对象，逻辑通常为 `upstream`。

# 返回格式要求
请直接返回纯净的 JSON 格式，**严禁**包含 Markdown 标记（如 ```json）。
示例：
{
  "ids_manifest": {
    "role": "processor",
    "ids_compliance": { ... }
  }
}
"""

def clean_html(text):
    """去除HTML标签，保留纯文本"""
    if not isinstance(text, str):
        return ""
    text = text.replace("<code>", "`").replace("</code>", "`")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<br>", " ").replace("\n", " ")
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def clean_json_response(response_text):
    """
    清洗 LLM 返回的内容，提取 JSON 部分。
    解决 DeepSeek 偶尔输出 Markdown 代码块的问题。
    """
    text = response_text.strip()
    # 模式1: ```json ... ```
    if "```json" in text:
        pattern = r"```json(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
    # 模式2: ``` ... ```
    elif "```" in text:
        return text.replace("```", "").strip()
    
    # 模式3: 如果开头不是 {，尝试寻找第一个 { 和 最后一个 }
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            return text[start:end+1]
            
    return text

def generate_natural_description(param):
    """生成自然语言描述"""
    parts = []
    display_name = param.get('displayName', param.get('name', ''))
    if display_name: parts.append(f"参数名: {display_name}")
    
    desc = clean_html(param.get('description', ''))
    if desc: parts.append(f"作用: {desc}")
        
    hint = clean_html(param.get('hint', param.get('ai_hint', '')))
    if hint: parts.append(f"提示: {hint}")
        
    default_val = param.get('default')
    if default_val is not None and str(default_val) != "":
        parts.append(f"默认值: {default_val}")
        
    if param.get('required') is True: parts.append("(必填项)")
        
    return " | ".join(parts)

def process_parameters(params_list):
    """递归处理参数列表"""
    cleaned_params = {}
    
    if isinstance(params_list, dict):
        temp_list = []
        for key, val in params_list.items():
            val['name'] = key
            temp_list.append(val)
        params_list = temp_list

    if not isinstance(params_list, list):
        return {}

    for param in params_list:
        if not isinstance(param, dict): continue
        
        name = param.get('name') or param.get('id')
        if not name: continue
            
        param_obj = {
            "displayName": param.get('displayName', name),
            "name": name,
            "type": param.get('type'),
            "required": param.get('required', False),
            "default": param.get('default'),
            "description": clean_html(param.get('description', '')),
            "natural_language_description": generate_natural_description(param)
        }

        if 'options' in param and isinstance(param['options'], list):
            clean_options = []
            for opt in param['options']:
                if isinstance(opt, dict):
                    opt_desc = f"{opt.get('name')} (值: {opt.get('value')})"
                    if opt.get('description'):
                        opt_desc += f" - {clean_html(opt['description'])}"
                    clean_options.append(opt_desc)
            if clean_options:
                param_obj['available_options'] = clean_options

        cleaned_params[name] = param_obj
        
    return cleaned_params

def generate_ids_manifest(node_data):
    """
    调用 SiliconFlow DeepSeek-V3 生成 IDS Manifest
    """
    if not ENABLE_AI or not client:
        return None

    try:
        # 构建精简的上下文，减少 Token 消耗
        minimized_context = {
            "node_name": node_data.get("name"),
            "description": node_data.get("semantic_context"),
            # 只取前 15 个参数，防止 Context 过长，通常前几个是最重要的
            "core_parameters": dict(list(node_data.get("parameters", {}).items())[:15]) 
        }
        
        context_str = json.dumps(minimized_context, ensure_ascii=False)

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": IDS_SYSTEM_PROMPT},
                {"role": "user", "content": f"Analyze this node and generate ids_manifest:\n\n{context_str}"}
            ],
            temperature=0.1,  # 低温度保证输出稳定
            max_tokens=600,
            stream=False
        )
        
        content = response.choices[0].message.content
        cleaned_json_str = clean_json_response(content)
        
        result = json.loads(cleaned_json_str)
        return result.get("ids_manifest")

    except json.JSONDecodeError:
        print("  ⚠️ JSON 解析失败 (AI 返回格式错误)")
        return None
    except Exception as e:
        print(f"  ⚠️ AI API 调用失败: {e}")
        return None

def main():
    # 0. 检查源文件
    if not SRC_FILE.exists():
        print(f"❌ 错误: 源文件未找到: {SRC_FILE}")
        print("请创建一个 'raw' 文件夹，并将 n8n 节点库 JSON 放入其中。")
        return

    # 1. 准备输出目录
    if OUTPUT_DIR.exists():
        try:
            shutil.rmtree(OUTPUT_DIR)
        except Exception as e:
            print(f"清理输出目录失败: {e}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"📂 读取源文件: {SRC_FILE}...")
    
    try:
        with open(SRC_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # 兼容列表或字典包裹的 JSON 结构
        nodes_data = []
        if isinstance(data, list):
            nodes_data = data
        elif isinstance(data, dict):
            for key in ['nodes', 'items', 'elements']:
                if key in data and isinstance(data[key], list):
                    nodes_data = data[key]
                    break
        
        if not nodes_data:
            print("⚠️ 警告: JSON 文件中未找到节点数据列表。")
            return

        total = len(nodes_data)
        print(f"🚀 开始处理 {total} 个节点 (AI增强: {'开启' if ENABLE_AI else '关闭'})...\n")
        
        success_count = 0
        
        for idx, node in enumerate(nodes_data):
            if not isinstance(node, dict): continue
            
            # --- Step 1: 基础清洗 ---
            raw_name = node.get('name') or node.get('displayName') or f"node_{idx}"
            safe_filename = re.sub(r"[\\/:*?\"<>|]+", "_", raw_name).replace(" ", "_").strip()
            
            # 打印进度
            print(f"[{idx+1}/{total}] 处理: {raw_name:<30}", end="", flush=True)

            clean_node = {
                "node_id": raw_name,
                "name": node.get('displayName', raw_name),
                "version": node.get('defaults', {}).get('version', 1),
                "semantic_context": clean_html(node.get('description', '')),
                "natural_language_description": f"这是 {node.get('displayName')} 节点。主要用于: {clean_html(node.get('description', ''))}",
                "parameters": {},
            }
            
            # --- Step 2: 参数提取 ---
            raw_params = node.get('properties') or node.get('parameters') or []
            if raw_params:
                clean_node['parameters'] = process_parameters(raw_params)
            
            # --- Step 3: AI 生成 IDS Manifest (核心) ---
            if ENABLE_AI:
                manifest = generate_ids_manifest(clean_node)
                if manifest:
                    clean_node['ids_manifest'] = manifest
                    print("✅ IDS 注入成功", end="")
                else:
                    print("⚠️ IDS 生成跳过", end="")
                
                # 🛑 关键：简单的流控，防止 SiliconFlow 报 429 错误
                # DeepSeek V3 生成速度很快，稍微停顿一下比较安全
                time.sleep(0.3) 
            
            print("") # 换行

            # --- Step 4: 保存文件 ---
            file_path = OUTPUT_DIR / f"{safe_filename}.json"
            with open(file_path, 'w', encoding='utf-8') as out_f:
                json.dump(clean_node, out_f, indent=2, ensure_ascii=False)
            
            success_count += 1

        print(f"\n🎉 处理完成! 成功生成 {success_count} 个节点文件。")
        print(f"📂 输出目录: {OUTPUT_DIR.absolute()}")

    except Exception as e:
        print(f"\n❌ 致命错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()