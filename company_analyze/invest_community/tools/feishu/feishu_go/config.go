package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

const defaultBaseURL = "https://open.feishu.cn/open-apis"

// Config holds environment settings loaded from .env and process env.
type Config struct {
	BaseURL               string
	AppID                 string
	AppSecret             string
	SpaceID               string
	ParentNodeToken       string
	NodeType              string
	ObjType               string
	ParentBlockID         string
	ChildrenID            string
	BitableNodeToken      string
	BitableTableID        string
	DocumentURL           string
	MarkdownPath          string
	RequestTimeoutSeconds int
	InsecureSSL           bool
	DisableProxy          bool
	Debug                 bool
}

func loadDotEnv() {
	root := filepath.Join("..", "..", "..")
	path := filepath.Join(root, ".env")
	data, err := os.ReadFile(path)
	if err != nil {
		return
	}
	lines := strings.Split(string(data), "\n")
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") || !strings.Contains(line, "=") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		val = strings.Trim(val, "\"'")
		if os.Getenv(key) == "" {
			_ = os.Setenv(key, val)
		}
	}
}

func loadConfig() Config {
	baseURL := getEnv("FEISHU_BASE_URL", defaultBaseURL)
	baseURL = strings.TrimSuffix(baseURL, "/open-apis")
	return Config{
		BaseURL:               baseURL,
		AppID:                 os.Getenv("FEISHU_APP_ID"),
		AppSecret:             os.Getenv("FEISHU_APP_SECRET"),
		SpaceID:               os.Getenv("FEISHU_SPACE_ID"),
		ParentNodeToken:       os.Getenv("FEISHU_PARENT_NODE_TOKEN"),
		NodeType:              getEnv("FEISHU_NODE_TYPE", "origin"),
		ObjType:               getEnv("FEISHU_OBJ_TYPE", "docx"),
		ParentBlockID:         os.Getenv("FEISHU_PARENT_BLOCK_ID"),
		ChildrenID:            os.Getenv("FEISHU_CHILDREN_ID"),
		BitableNodeToken:      os.Getenv("FEISHU_BITABLE_NODE_TOKEN"),
		BitableTableID:        os.Getenv("FEISHU_BITABLE_TABLE_ID"),
		DocumentURL:           os.Getenv("FEISHU_DOCUMENT_URL"),
		MarkdownPath:          os.Getenv("FEISHU_MARKDOWN_PATH"),
		RequestTimeoutSeconds: getEnvInt("FEISHU_REQUEST_TIMEOUT", 30),
		InsecureSSL:           os.Getenv("FEISHU_INSECURE_SSL") == "1",
		DisableProxy:          os.Getenv("FEISHU_DISABLE_PROXY") == "1",
		Debug:                 os.Getenv("FEISHU_DEBUG") == "1",
	}
}

func getEnv(key, def string) string {
	val := os.Getenv(key)
	if val == "" {
		return def
	}
	return val
}

func getEnvInt(key string, def int) int {
	val := os.Getenv(key)
	if val == "" {
		return def
	}
	var out int
	_, err := fmt.Sscanf(val, "%d", &out)
	if err != nil {
		return def
	}
	return out
}
