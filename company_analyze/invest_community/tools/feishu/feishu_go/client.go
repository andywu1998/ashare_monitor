package main

import (
	"crypto/tls"
	"net/http"
	"time"

	lark "github.com/larksuite/oapi-sdk-go/v3"
)

func newSDKClient(cfg Config) *lark.Client {
	options := []lark.ClientOptionFunc{
		lark.WithOpenBaseUrl(cfg.BaseURL),
		lark.WithReqTimeout(time.Duration(cfg.RequestTimeoutSeconds) * time.Second),
	}

	if cfg.InsecureSSL || cfg.DisableProxy {
		tr := &http.Transport{}
		if cfg.InsecureSSL {
			tr.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
		}
		if cfg.DisableProxy {
			tr.Proxy = nil
		}
		httpClient := &http.Client{
			Timeout:   time.Duration(cfg.RequestTimeoutSeconds) * time.Second,
			Transport: tr,
		}
		options = append(options, lark.WithHttpClient(httpClient))
	}

	return lark.NewClient(cfg.AppID, cfg.AppSecret, options...)
}
