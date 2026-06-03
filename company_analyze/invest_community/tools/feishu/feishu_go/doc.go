package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	lark "github.com/larksuite/oapi-sdk-go/v3"
	larkdocx "github.com/larksuite/oapi-sdk-go/v3/service/docx/v1"
	larkwiki "github.com/larksuite/oapi-sdk-go/v3/service/wiki/v2"
)

func runCreateDoc(ctx context.Context, client *lark.Client, cfg Config) error {
	mdPath, err := resolveMarkdownPath(cfg.MarkdownPath)
	if err != nil {
		return err
	}
	mdBytes, err := os.ReadFile(mdPath)
	if err != nil {
		return err
	}
	mdText := string(mdBytes)
	if cfg.Debug {
		fmt.Println("Using markdown:", mdPath)
	}

	docID, err := createWikiNode(ctx, client, cfg, filepath.Base(mdPath))
	if err != nil {
		return err
	}

	blocks, err := convertMarkdown(ctx, client, mdText)
	if err != nil {
		return err
	}

	revID, _ := getDocumentRevisionID(ctx, client, docID)
	if err := createBlocks(ctx, client, cfg, docID, revID, blocks); err != nil {
		return err
	}

	fmt.Println("文档创建完成:", docID)
	return nil
}

func resolveMarkdownPath(explicit string) (string, error) {
	if explicit != "" {
		return explicit, nil
	}
	files, _ := filepath.Glob("*.md")
	if len(files) == 0 {
		return "", errors.New("当前目录没有找到 .md 文件")
	}
	return files[0], nil
}

func createWikiNode(ctx context.Context, client *lark.Client, cfg Config, title string) (string, error) {
	if cfg.SpaceID == "" || cfg.ParentNodeToken == "" {
		return "", errors.New("缺少 FEISHU_SPACE_ID 或 FEISHU_PARENT_NODE_TOKEN")
	}

	node := larkwiki.NewNodeBuilder().
		ParentNodeToken(cfg.ParentNodeToken).
		NodeType(cfg.NodeType).
		ObjType(cfg.ObjType).
		Title(title).
		Build()

	req := larkwiki.NewCreateSpaceNodeReqBuilder().
		SpaceId(cfg.SpaceID).
		Node(node).
		Build()

	resp, err := client.Wiki.V2.SpaceNode.Create(ctx, req)
	if err != nil {
		return "", err
	}
	if !resp.Success() || resp.Data == nil || resp.Data.Node == nil || resp.Data.Node.ObjToken == nil {
		return "", fmt.Errorf("create node failed: %v", resp)
	}
	return *resp.Data.Node.ObjToken, nil
}

func convertMarkdown(ctx context.Context, client *lark.Client, text string) ([]*larkdocx.Block, error) {
	body := larkdocx.NewConvertDocumentReqBodyBuilder().
		ContentType("markdown").
		Content(text).
		Build()

	req := larkdocx.NewConvertDocumentReqBuilder().
		Body(body).
		Build()

	resp, err := client.Docx.V1.Document.Convert(ctx, req)
	if err != nil || !resp.Success() || resp.Data == nil || len(resp.Data.Blocks) == 0 {
		return simpleMarkdownBlocks(text), nil
	}
	return sanitizeBlocks(resp.Data.Blocks), nil
}

func sanitizeBlocks(blocks []*larkdocx.Block) []*larkdocx.Block {
	allowed := map[int]bool{2: true, 3: true, 4: true, 5: true}
	cleaned := []*larkdocx.Block{}
	for _, b := range blocks {
		if b == nil || b.BlockType == nil {
			continue
		}
		if !allowed[*b.BlockType] {
			continue
		}
		b.BlockId = nil
		b.ParentId = nil
		b.DocumentId = nil
		b.TableCell = nil
		b.Children = nil
		cleaned = append(cleaned, b)
	}
	return cleaned
}

func simpleMarkdownBlocks(text string) []*larkdocx.Block {
	lines := strings.Split(text, "\n")
	blocks := []*larkdocx.Block{}
	for _, raw := range lines {
		line := strings.TrimSpace(raw)
		if line == "" {
			continue
		}
		switch {
		case strings.HasPrefix(line, "# "):
			blocks = append(blocks, buildTextBlock(3, line[2:]))
		case strings.HasPrefix(line, "## "):
			blocks = append(blocks, buildTextBlock(4, line[3:]))
		case strings.HasPrefix(line, "### "):
			blocks = append(blocks, buildTextBlock(5, line[4:]))
		default:
			blocks = append(blocks, buildTextBlock(2, line))
		}
	}
	return blocks
}

func buildTextBlock(blockType int, content string) *larkdocx.Block {
	text := larkdocx.NewTextBuilder().
		Elements([]*larkdocx.TextElement{
			larkdocx.NewTextElementBuilder().
				TextRun(larkdocx.NewTextRunBuilder().Content(content).Build()).
				Build(),
		}).
		Build()

	builder := larkdocx.NewBlockBuilder().BlockType(blockType)
	switch blockType {
	case 2:
		builder.Text(text)
	case 3:
		builder.Heading1(text)
	case 4:
		builder.Heading2(text)
	case 5:
		builder.Heading3(text)
	default:
		builder.Text(text)
	}
	return builder.Build()
}

func getDocumentRevisionID(ctx context.Context, client *lark.Client, documentID string) (int, error) {
	req := larkdocx.NewGetDocumentReqBuilder().DocumentId(documentID).Build()
	resp, err := client.Docx.V1.Document.Get(ctx, req)
	if err != nil || !resp.Success() || resp.Data == nil || resp.Data.Document == nil || resp.Data.Document.RevisionId == nil {
		return 0, err
	}
	return *resp.Data.Document.RevisionId, nil
}

func createBlocks(ctx context.Context, client *lark.Client, cfg Config, documentID string, revisionID int, blocks []*larkdocx.Block) error {
	parentBlockID := cfg.ParentBlockID
	if parentBlockID == "" {
		parentBlockID = documentID
	}
	batchSize := 50
	for i := 0; i < len(blocks); i += batchSize {
		end := i + batchSize
		if end > len(blocks) {
			end = len(blocks)
		}
		body := larkdocx.NewCreateDocumentBlockChildrenReqBodyBuilder().
			Children(blocks[i:end]).
			Index(0).
			Build()
		req := larkdocx.NewCreateDocumentBlockChildrenReqBuilder().
			DocumentId(documentID).
			BlockId(parentBlockID).
			DocumentRevisionId(revisionID).
			Body(body).
			Build()
		resp, err := client.Docx.V1.DocumentBlockChildren.Create(ctx, req)
		if err == nil && resp.Success() {
			continue
		}
		return createDescendant(ctx, client, cfg, documentID, revisionID, blocks)
	}
	return nil
}

func createDescendant(ctx context.Context, client *lark.Client, cfg Config, documentID string, revisionID int, blocks []*larkdocx.Block) error {
	parentBlockID := cfg.ParentBlockID
	if parentBlockID == "" {
		parentBlockID = documentID
	}
	childrenID := cfg.ChildrenID
	if childrenID == "" {
		childrenID = parentBlockID
	}
	heading := buildTextBlock(3, "研报内容")
	descendants := append([]*larkdocx.Block{heading}, blocks...)
	body := larkdocx.NewCreateDocumentBlockDescendantReqBodyBuilder().
		ChildrenId([]string{childrenID}).
		Index(0).
		Descendants(descendants).
		Build()
	req := larkdocx.NewCreateDocumentBlockDescendantReqBuilder().
		DocumentId(documentID).
		BlockId(parentBlockID).
		DocumentRevisionId(revisionID).
		Body(body).
		Build()
	resp, err := client.Docx.V1.DocumentBlockDescendant.Create(ctx, req)
	if err != nil {
		return err
	}
	if !resp.Success() {
		return fmt.Errorf("descendant error: %v", resp)
	}
	return nil
}
