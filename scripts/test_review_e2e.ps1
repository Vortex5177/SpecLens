# Phase 7~10 端到端测试：上传样例项目 -> 确认版本 -> 执行 Code Review
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$root = "c:\Users\29461\Documents\Qoder\2026-09-01\chat-2"
$py = "$root\backend\.venv\Scripts\python.exe"

# 1. 生成样例项目（fastapi==0.120.0，与知识库版本对齐）
& $py "$root\scripts\make_sample.py"

# 2. 打包 zip（文件位于压缩包根目录）
$zip = "$root\test_sample\demo.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
& $py -c @"
import zipfile, pathlib
root = pathlib.Path(r'$root/test_sample/demo-project')
with zipfile.ZipFile(r'$zip', 'w') as zf:
    for p in root.rglob('*'):
        if p.is_file():
            zf.write(p, p.relative_to(root).as_posix())
print('[OK] zip created')
"@

# 3. 上传
curl.exe -s -X POST -F "file=@$($zip -replace '\\','/')" "http://localhost:8000/api/projects/upload" -o "$root\test_sample\upload.json"
$up = Get-Content "$root\test_sample\upload.json" -Raw -Encoding UTF8 | ConvertFrom-Json
$pid2 = $up.project_id
"[OK] project_id = $pid2"
"detected versions: " + (($up.analysis.versions | ForEach-Object { "$($_.technology)=$($_.version)[$($_.status)]" }) -join ", ")

# 4. 确认全部版本（含 uvicorn 范围约束，指定为 0.30.0）
# 注意：PS 5.1 向 curl 传带双引号的字符串会剥引号，改用文件作请求体
class VersionSelection { $technology; $version; VersionSelection($t, $v) { $this.technology = $t; $this.version = $v } }
$confirmBody = @{ versions = @(
    [VersionSelection]::new("fastapi", "0.120.0"),
    [VersionSelection]::new("uvicorn", "0.30.0"),
    [VersionSelection]::new("react", "18.2.0")
) } | ConvertTo-Json -Compress
Set-Content -Path "$root\test_sample\confirm_body.json" -Value $confirmBody -Encoding ASCII
curl.exe -s -X POST -H "Content-Type: application/json" --data-binary "@$root/test_sample/confirm_body.json" "http://localhost:8000/api/projects/$pid2/versions" -o "$root\test_sample\confirm.json"
$cf = Get-Content "$root\test_sample\confirm.json" -Raw -Encoding UTF8 | ConvertFrom-Json
"[OK] confirmed: " + (($cf.versions | ForEach-Object { "$($_.technology)=$($_.version) confirmed=$($_.confirmed)" }) -join ", ")

# 5. 执行 Code Review（LLM + Agent + RAG，可能耗时 1~3 分钟）
"[..] running review ..."
Set-Content -Path "$root\test_sample\review_body.json" -Value (@{ project_id = $pid2 } | ConvertTo-Json -Compress) -Encoding ASCII
curl.exe -s --max-time 600 -X POST -H "Content-Type: application/json" --data-binary "@$root/test_sample/review_body.json" "http://localhost:8000/api/reviews" -o "$root\test_sample\review.json"
$rv = Get-Content "$root\test_sample\review.json" -Raw -Encoding UTF8 | ConvertFrom-Json
"[OK] review done"
"summary: " + $rv.result.summary
"issues: " + $rv.result.issues.Count
foreach ($i in $rv.result.issues) {
    "- [$($i.category)/$($i.severity)/$($i.confidence)] $($i.file) -> $($i.title) | source=$($i.source)"
}
$saved = Get-Content "$root\uploads\$pid2\review.json" -Raw -Encoding UTF8 | ConvertFrom-Json
"project_fix_prompt length: " + $saved.project_fix_prompt.Length
"fix_prompt of issue #1 length: " + $rv.result.issues[0].fix_prompt.Length
