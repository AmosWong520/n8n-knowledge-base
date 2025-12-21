import json
import os
import shutil
import re
from pathlib import Path

# =================配置区域=================
# 定义源文件路径 (单一大文件)
ROOT = Path(".")
SRC_FILE = ROOT / "raw" / "n8n_nodes_library.json"
OUTPUT_DIR = ROOT / "output"
# =========================================

def clean_html(text):
    """简单的去除HTML标签，保留纯文本"""
    if not isinstance(text, str):
        return ""
    text = text.replace("<code>", "`").replace("</code>", "`")
    text = text.replace("<b>", "**").replace("</b>", "**")
    text = text.replace("<br>", " ").replace("\n", " ")
    # 去除链接标签但保留文字
    text = re.sub(r'<a[^>]*>(.*?)</a>', r'\1', text)
    # 去除其他残留标签
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()

def generate_natural_description(param):
    """
    【核心逻辑】这就是你问的“谁生成的描述”
    答案：这里通过代码，把分散的字段拼成了一句话。
    """
    parts = []
    
    # 1. 拿名字
    display_name = param.get('displayName', param.get('name', ''))
    if display_name:
        parts.append(f"参数名: {display_name}")
    
    # 2. 拿描述 (去掉原本难懂的 HTML 标签)
    desc = clean_html(param.get('description', ''))
    if desc:
        parts.append(f"作用: {desc}")
        
    # 3. 拿提示 (Hint)
    hint = clean_html(param.get('hint', param.get('ai_hint', ''))) # 兼容旧脚本的 ai_hint
    if hint:
        parts.append(f"提示: {hint}")
        
    # 4. 拿默认值
    default_val = param.get('default')
    if default_val is not None and str(default_val) != "":
        parts.append(f"默认值: {default_val}")
        
    # 5. 看看是不是必填
    if param.get('required') is True:
        parts.append("(必填项)")
        
    # 拼成一句话返回
    return " | ".join(parts)

def process_parameters(params_list):
    """递归处理参数列表"""
    cleaned_params = {}
    
    # 如果参数是字典形式（旧格式兼容）
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
            
        # 基础提取
        param_obj = {
            "displayName": param.get('displayName', name),
            "name": name,
            "type": param.get('type'),
            "required": param.get('required', False),
            "default": param.get('default'),
            "description": clean_html(param.get('description', '')),
            # === ✨ 就在这里增加了新字段 ✨ ===
            "natural_language_description": generate_natural_description(param)
        }

        # 处理选项 (Options)
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

def main():
    if not SRC_FILE.exists():
        print(f"Error: Source file {SRC_FILE} not found.")
        return

    # 清理并重建输出目录
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Reading source file: {SRC_FILE}...")
    
    try:
        # 读取原始大文件
        with open(SRC_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # 兼容处理：有时候 source 是 list，有时候可能是 dict
        nodes_data = []
        if isinstance(data, list):
            nodes_data = data
        elif isinstance(data, dict):
            # 尝试找 nodes 列表
            for key in ['nodes', 'items', 'elements']:
                if key in data and isinstance(data[key], list):
                    nodes_data = data[key]
                    break
        
        if not nodes_data:
            print("Warning: No nodes found in JSON file.")
            return

        count = 0
        for node in nodes_data:
            if not isinstance(node, dict): continue
            
            # 1. 确定文件名
            raw_name = node.get('name') or node.get('displayName') or f"node_{count}"
            # 简单的文件名清洗
            safe_filename = re.sub(r"[\\/:*?\"<>|]+", "_", raw_name).replace(" ", "_").strip()
            
            # 2. 提取核心数据
            clean_node = {
                "node_id": raw_name,
                "name": node.get('displayName', raw_name),
                "version": node.get('defaults', {}).get('version', 1),
                "semantic_context": clean_html(node.get('description', '')),
                # 节点级别的描述也拼一下
                "natural_language_description": f"这是 {node.get('displayName')} 节点。主要用于: {clean_html(node.get('description', ''))}",
                "parameters": {},
            }
            
            # 3. 处理参数
            raw_params = node.get('properties') or node.get('parameters') or []
            if raw_params:
                clean_node['parameters'] = process_parameters(raw_params)
            
            # 4. 写入文件
            file_path = OUTPUT_DIR / f"{safe_filename}.json"
            with open(file_path, 'w', encoding='utf-8') as out_f:
                json.dump(clean_node, out_f, indent=2, ensure_ascii=False)
            
            count += 1

        print(f"Success! Processed {count} nodes. Output written to {OUTPUT_DIR}/")

    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()