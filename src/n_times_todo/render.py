"""Human-readable materialized views. Content is escaped, never executed."""
import json
import string


def markdown(snapshot):
    # HTML escaping makes task text inert even in Markdown viewers accepting HTML.
    def literal(value):
        return "".join("<br>" if ch == "\n" else f"&#{ord(ch)};" if ch in string.punctuation else ch
                       for ch in str(value))
    rows = [f'# N次贴 · v{snapshot["version"]}', '',
            f'UTC: {snapshot["created_at"]}',
            f'Kind: {snapshot["kind"]}; parent: {snapshot["parent_version"]}',
            f'SHA-256: {snapshot["hash"]}', '',
            '## 更新说明', literal(snapshot['reason']), '', '## 完整待办快照', '']
    for task in snapshot['tasks']:
        rows.extend([f'- [{"x" if task["status"] == "done" else " "}] '
                     f'**{literal(task["title"])}** ({literal(task["id"])}, {task["status"]}',
                     f'  <pre>{literal(task["notes"])}</pre>' if task['notes'] else ''])
    if not snapshot['tasks']:
        rows.append('（空清单）')
    rows.extend(['', '## 相对上一版的自动标注', ''])
    change = snapshot['changes']
    for task in change['added']:
        rows.append(f'- 新增 {literal(task["id"])}: {literal(task["title"])}')
    for task in change['removed']:
        rows.append(f'- 删除 {literal(task["id"])}: {literal(task["title"])}（旧版仍保留）')
    for item in change['modified']:
        for field, values in item['fields'].items():
            rows.append(f'- 修改 {literal(item["id"])}.{field}: '
                        f'<del>{literal(values["before"])}</del> → <ins>{literal(values["after"])}</ins>')
    for label, key in [('完成', 'completed'), ('重新打开', 'reopened')]:
        if change[key]:
            rows.append(f'- {label}: ' + ', '.join(map(literal, change[key])))
    if change['order']:
        rows.append('- 排序: ' + literal(' → '.join(change['order']['before'])) +
                    ' ⇒ ' + literal(' → '.join(change['order']['after'])))
    if not any(change.values()):
        rows.append('（内容未改变；此次明确更新仍追加版本）')
    rows.extend(['', '历史版本不因删除或恢复而覆盖。本页是完整版本的可再生视图。', ''])
    return '\n'.join(rows)


def json_document(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'
