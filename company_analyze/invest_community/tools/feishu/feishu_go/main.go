package main

import (
	"context"
	"flag"
	"fmt"
	"os"
)

func main() {
	loadDotEnv()
	cfg := loadConfig()

	cmd := flag.String("cmd", "", "command: create-doc | schema | insert")
	fields := flag.String("fields", "", "JSON fields for bitable insert")
	mdPath := flag.String("md", "", "Markdown file path (optional)")
	flag.Parse()

	if *cmd == "" {
		fmt.Println("Usage: go run . -cmd=create-doc | schema | insert")
		os.Exit(1)
	}

	if *mdPath != "" {
		cfg.MarkdownPath = *mdPath
	}

	ctx := context.Background()
	client := newSDKClient(cfg)

	var err error
	switch *cmd {
	case "create-doc":
		err = runCreateDoc(ctx, client, cfg)
	case "schema":
		err = runSchema(ctx, client, cfg)
	case "insert":
		err = runInsert(ctx, client, cfg, *fields)
	default:
		fmt.Println("Unknown cmd:", *cmd)
		os.Exit(1)
	}

	if err != nil {
		fmt.Println("ERROR:", err)
		os.Exit(1)
	}
}
