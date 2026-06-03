#!/bin/bash

# 批量上传 markdown 文件到飞书
# 使用方法: ./batch_upload_to_feishu.sh

# 设置颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 报告目录
REPORTS_DIR="/Users/andy/code/invest/invest_community/tools/company_report/reports"
# 飞书脚本路径
FEISHU_SCRIPT="/Users/andy/code/invest/invest_community/tools/feishu/create_doc_from_md.py"

# 检查目录是否存在
if [ ! -d "$REPORTS_DIR" ]; then
    echo -e "${RED}错误: 报告目录不存在: $REPORTS_DIR${NC}"
    exit 1
fi

# 检查脚本是否存在
if [ ! -f "$FEISHU_SCRIPT" ]; then
    echo -e "${RED}错误: 飞书脚本不存在: $FEISHU_SCRIPT${NC}"
    exit 1
fi

# 统计变量
total=0
success=0
failed=0

# 获取所有 markdown 文件
echo -e "${GREEN}开始批量上传 markdown 文件到飞书...${NC}"
echo "报告目录: $REPORTS_DIR"
echo "----------------------------------------"

# 遍历所有 markdown 文件
for md_file in "$REPORTS_DIR"/*.md; do
    # 检查文件是否存在（处理没有匹配文件的情况）
    if [ ! -f "$md_file" ]; then
        echo -e "${YELLOW}警告: 没有找到 markdown 文件${NC}"
        break
    fi

    total=$((total + 1))
    filename=$(basename "$md_file")

    echo -e "\n${YELLOW}[$total] 处理文件: $filename${NC}"

    # 设置环境变量并执行脚本
    export FEISHU_MARKDOWN_PATH="$md_file"

    if python3 "$FEISHU_SCRIPT"; then
        echo -e "${GREEN}✓ 成功上传: $filename${NC}"
        success=$((success + 1))
    else
        echo -e "${RED}✗ 上传失败: $filename${NC}"
        failed=$((failed + 1))
    fi

    # 添加延迟以避免 API 限流
    sleep 2
done

# 输出统计信息
echo ""
echo "========================================"
echo -e "${GREEN}批量上传完成！${NC}"
echo "总文件数: $total"
echo -e "${GREEN}成功: $success${NC}"
echo -e "${RED}失败: $failed${NC}"
echo "========================================"

exit 0
