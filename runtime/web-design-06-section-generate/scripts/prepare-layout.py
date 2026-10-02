#!/usr/bin/env python3
"""目視した直前2枚に基づき、未実行promptの配置とhashだけを更新する。"""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

RIGHT_TEXT = "見出し・本文・ボタンを右側にまとめ、写真・イラストを左側に配置した2カラム構成にする。"


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def prepare_layout(project, page, task_num, previous_sides, suitable, reason):
    task_dir = Path(project).resolve() / "_imagegen" / page
    manifest_path = task_dir / "manifest.json"
    spec_path = task_dir / "prompt-spec.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    index = next(i for i, item in enumerate(manifest) if item["num"] == task_num)
    item = manifest[index]
    prompt_path = Path(item["individual_prompt_file"])
    if spec.get("mode") != "normal":
        raise ValueError("配置調整は通常生成の未実行対象だけに適用できます")
    if Path(item["expected_output"]).exists() or item.get("actual_image_input", "pending") != "pending":
        raise ValueError("実行済み対象のpromptは変更できません")
    log_path = task_dir / "generation-log.jsonl"
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            numbers = [row.get(key) for key in ("num", "task_num", "task_number")]
            if str(task_num) in [str(n) for n in numbers] or str(prompt_path) in json.dumps(row, ensure_ascii=False):
                raise ValueError("試行記録のある対象のpromptは変更できません")
            if not any(n is not None for n in numbers):
                raise ValueError("ログの対象番号を確認できません。生成記録を確認してください")
    for later in manifest[index + 1:]:
        if Path(later["expected_output"]).exists():
            raise ValueError("生成順に次の未実行対象だけを調整してください")
    fv = {"num": 0, "expected_output": item["style_ref"]}
    previous = ([fv] + manifest[:index])[-2:]
    if len(previous_sides) != len(previous) or any(s not in {"left", "right", "center", "other"} for s in previous_sides):
        raise ValueError("直前の最大2枚について、古い順に目視した位置を指定してください")
    if not reason.strip():
        raise ValueError("2カラム適性と明示指定との整合性の判断理由が必要です")
    for earlier in [fv] + manifest[:index]:
        with Image.open(earlier["expected_output"]) as image:
            image.verify()
    if sha256(Path(fv["expected_output"]).read_bytes()) != item["reference_image_sha256"]:
        raise ValueError("採用FVのhashがmanifestと一致しません")
    observations = [
        {"num": earlier["num"], "text_side": side,
         "image_sha256": sha256(Path(earlier["expected_output"]).read_bytes())}
        for earlier, side in zip(previous, previous_sides)
    ]
    prompt = prompt_path.read_bytes()
    if sha256(prompt) != item["prompt_sha256"]:
        raise ValueError("個別promptとmanifestのhashが一致しません")
    spec_item = next(section for section in spec["sections"] if section["num"] == task_num)
    if spec_item["prompt_sha256"] != item["prompt_sha256"]:
        raise ValueError("prompt-specとmanifestのhashが一致しません")
    instruction = RIGHT_TEXT if previous_sides == ["left", "left"] and suitable else ""
    decision = {"count_includes_fv": True, "previous_images": observations, "two_column_suitable": suitable,
                "reason": reason, "instruction": instruction}
    if "runtime_layout" in item:
        prior = dict(item["runtime_layout"])
        prior.pop("base_prompt_sha256")
        if prior != decision or spec_item.get("runtime_layout") != item["runtime_layout"]:
            raise ValueError("保存済みの配置判断と異なります。判断済みpromptを上書きしません")
        return instruction
    decision["base_prompt_sha256"] = item["prompt_sha256"]
    if instruction:
        marker = "\n## 表示文字\n".encode("utf-8")
        if prompt.count(marker) != 1:
            raise ValueError("個別Markdownの表示文字ブロックを特定できません")
        prompt = prompt.replace(marker, ("- " + instruction + "\n").encode("utf-8") + marker)
    for target in (item, spec_item):
        target["runtime_layout"] = decision
        target["prompt_sha256"] = sha256(prompt)
    # すべての検証を済ませてから、未実行対象とその管理情報だけを保存する。
    prompt_path.write_bytes(prompt)
    spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return instruction


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project")
    parser.add_argument("page")
    parser.add_argument("task_num", type=int)
    parser.add_argument("--previous-text-sides", nargs="*", default=[])
    parser.add_argument("--two-column-suitable", choices=("yes", "no"), required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        instruction = prepare_layout(args.project, args.page, args.task_num, args.previous_text_sides,
                                     args.two_column_suitable == "yes", args.reason)
    except (ValueError, OSError, KeyError, StopIteration) as exc:
        parser.exit(1, f"配置判断を保存できません: {exc}\n")
    print(instruction or "配置指定は追加せず、元のpromptを使います。")


if __name__ == "__main__":
    main()
