package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	lark "github.com/larksuite/oapi-sdk-go/v3"
	larkbitable "github.com/larksuite/oapi-sdk-go/v3/service/bitable/v1"
	larkwiki "github.com/larksuite/oapi-sdk-go/v3/service/wiki/v2"
)

func runSchema(ctx context.Context, client *lark.Client, cfg Config) error {
	appToken, err := resolveBitableAppToken(ctx, client, cfg)
	if err != nil {
		return err
	}
	if cfg.BitableTableID == "" {
		return errors.New("缺少 FEISHU_BITABLE_TABLE_ID")
	}
	req := larkbitable.NewListAppTableFieldReqBuilder().
		AppToken(appToken).
		TableId(cfg.BitableTableID).
		Build()
	resp, err := client.Bitable.V1.AppTableField.List(ctx, req)
	if err != nil {
		return err
	}
	b, _ := json.MarshalIndent(resp, "", "  ")
	fmt.Println(string(b))
	return nil
}

func runInsert(ctx context.Context, client *lark.Client, cfg Config, fieldsJSON string) error {
	appToken, err := resolveBitableAppToken(ctx, client, cfg)
	if err != nil {
		return err
	}
	if cfg.BitableTableID == "" {
		return errors.New("缺少 FEISHU_BITABLE_TABLE_ID")
	}
	fields := map[string]any{}
	if fieldsJSON != "" {
		if err := json.Unmarshal([]byte(fieldsJSON), &fields); err != nil {
			return err
		}
	} else if cfg.DocumentURL != "" {
		fields["document_url"] = cfg.DocumentURL
	} else {
		return errors.New("未提供 fields JSON，且 FEISHU_DOCUMENT_URL 为空")
	}

	record := larkbitable.NewAppTableRecordBuilder().
		Fields(fields).
		Build()

	req := larkbitable.NewCreateAppTableRecordReqBuilder().
		AppToken(appToken).
		TableId(cfg.BitableTableID).
		AppTableRecord(record).
		Build()

	resp, err := client.Bitable.V1.AppTableRecord.Create(ctx, req)
	if err != nil {
		return err
	}
	b, _ := json.MarshalIndent(resp, "", "  ")
	fmt.Println(string(b))
	return nil
}

func resolveBitableAppToken(ctx context.Context, client *lark.Client, cfg Config) (string, error) {
	if cfg.SpaceID == "" || cfg.BitableNodeToken == "" {
		return "", errors.New("缺少 FEISHU_SPACE_ID 或 FEISHU_BITABLE_NODE_TOKEN")
	}
	req := larkwiki.NewGetNodeSpaceReqBuilder().
		Token(cfg.BitableNodeToken).
		Build()
	resp, err := client.Wiki.V2.Space.GetNode(ctx, req)
	if err != nil {
		return "", err
	}
	if resp.Data == nil || resp.Data.Node == nil || resp.Data.Node.ObjToken == nil {
		return "", fmt.Errorf("obj_token missing: %v", resp)
	}
	return *resp.Data.Node.ObjToken, nil
}
