import * as vscode from "vscode";
import { OFKMSClient, APIError, AuthenticationError } from "../api/client";
import type {
  ExtensionToWebviewMessage,
  WebviewToExtensionMessage,
} from "../api/types";

export class ChatViewProvider implements vscode.WebviewViewProvider {
  private view?: vscode.WebviewView;
  private client?: OFKMSClient;

  constructor(private readonly extensionUri: vscode.Uri) {}

  resolveWebviewView(webviewView: vscode.WebviewView) {
    this.view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        vscode.Uri.joinPath(this.extensionUri, "dist"),
        vscode.Uri.joinPath(this.extensionUri, "media"),
      ],
    };

    webviewView.webview.html = this.getHtml(webviewView.webview);

    webviewView.webview.onDidReceiveMessage(
      (msg: WebviewToExtensionMessage) => {
        this.handleMessage(msg);
      }
    );
  }

  // ── Public commands ──

  clearChat() {
    this.postMessage({ type: "clearChat" });
  }

  async checkHealth() {
    const client = this.getClient();
    try {
      const data = await client.health();
      this.postMessage({ type: "health", data });
    } catch (err) {
      this.sendError(err);
    }
  }

  onConfigChanged() {
    this.client = undefined; // force re-create
    const cfg = vscode.workspace.getConfiguration("ofkms");
    this.postMessage({
      type: "settings",
      data: {
        language: cfg.get<string>("defaultLanguage") ?? "",
        product: cfg.get<string>("defaultProduct") ?? "",
      },
    });
    this.checkHealth();
  }

  // ── Message handling ──

  private async handleMessage(msg: WebviewToExtensionMessage) {
    switch (msg.type) {
      case "ready":
        await this.onReady();
        break;
      case "query":
        await this.onQuery(msg.query, msg.language, msg.product);
        break;
      case "cancel":
        this.getClient().cancelStream();
        break;
      case "openSettings":
        vscode.commands.executeCommand(
          "workbench.action.openSettings",
          "ofkms"
        );
        break;
    }
  }

  private async onReady() {
    const cfg = vscode.workspace.getConfiguration("ofkms");
    this.postMessage({
      type: "settings",
      data: {
        language: cfg.get<string>("defaultLanguage") ?? "",
        product: cfg.get<string>("defaultProduct") ?? "",
      },
    });

    const client = this.getClient();

    // Fetch health + products in parallel
    try {
      const health = await client.health();
      this.postMessage({ type: "health", data: health });
    } catch (err) {
      this.sendError(err);
    }

    try {
      const products = await client.products();
      this.postMessage({ type: "products", data: products.products });
    } catch {
      // products fail is non-critical
    }
  }

  private async onQuery(query: string, language: string, product: string) {
    const client = this.getClient();

    try {
      await client.queryStream(
        query,
        language || undefined,
        product || undefined,
        {
          onPhase: (data) => this.postMessage({ type: "streamPhase", data }),
          onAnswer: (data) => this.postMessage({ type: "streamAnswer", data }),
          onDone: (data) => this.postMessage({ type: "streamDone", data }),
          onError: (data) =>
            this.postMessage({ type: "error", message: data.error }),
        }
      );
    } catch (err) {
      this.sendError(err);
    }
  }

  // ── Helpers ──

  private getClient(): OFKMSClient {
    if (!this.client) {
      const cfg = vscode.workspace.getConfiguration("ofkms");
      this.client = new OFKMSClient(
        cfg.get<string>("apiUrl") ?? "http://192.168.8.11:12830",
        cfg.get<string>("apiKey") ?? "",
        cfg.get<number>("timeout") ?? 120
      );
    }
    return this.client;
  }

  private postMessage(msg: ExtensionToWebviewMessage) {
    this.view?.webview.postMessage(msg);
  }

  private sendError(err: unknown) {
    let message = "Unknown error";
    if (err instanceof AuthenticationError) {
      message = `${err.message}. Click the gear icon to configure your API key.`;
    } else if (err instanceof APIError) {
      message = err.hint ? `${err.message} — ${err.hint}` : err.message;
    } else if (err instanceof Error) {
      message = err.message;
    }
    this.postMessage({ type: "error", message });
  }

  // ── HTML ──

  private getHtml(webview: vscode.Webview): string {
    const chatJsUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "dist", "chat.js")
    );
    const chatCssUri = webview.asWebviewUri(
      vscode.Uri.joinPath(this.extensionUri, "media", "chat.css")
    );
    const nonce = getNonce();

    return /*html*/ `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="Content-Security-Policy"
    content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}'; font-src ${webview.cspSource};">
  <link rel="stylesheet" href="${chatCssUri}">
  <title>OFKMS Chat</title>
</head>
<body>
  <div id="app">
    <!-- Header -->
    <div class="header">
      <div class="header-left">
        <span class="title">OFKMS</span>
        <span id="healthDot" class="health-dot unknown" title="Checking..."></span>
      </div>
      <div class="header-right">
        <button id="btnClear" class="icon-btn" title="Clear chat">
          <svg width="16" height="16" viewBox="0 0 16 16"><path fill="currentColor" d="M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1zm3.5 9.5l-1 1L8 9l-2.5 2.5-1-1L7 8 4.5 5.5l1-1L8 7l2.5-2.5 1 1L9 8l2.5 2.5z"/></svg>
        </button>
        <button id="btnSettings" class="icon-btn" title="Settings">
          <svg width="16" height="16" viewBox="0 0 16 16"><path fill="currentColor" d="M9.1 4.4L8.6 2H7.4l-.5 2.4-.7.3-2-1.3-.9.8 1.3 2-.3.7L2 7.4v1.2l2.4.5.3.7-1.3 2 .8.9 2-1.3.7.3.5 2.4h1.2l.5-2.4.7-.3 2 1.3.9-.8-1.3-2 .3-.7 2.4-.5V7.4l-2.4-.5-.3-.7 1.3-2-.8-.9-2 1.3-.7-.3zM8 10a2 2 0 1 1 0-4 2 2 0 0 1 0 4z"/></svg>
        </button>
      </div>
    </div>

    <!-- Filters -->
    <div class="filters">
      <select id="selProduct" title="Product filter">
        <option value="">All Products</option>
      </select>
      <select id="selLanguage" title="Response language">
        <option value="">Auto</option>
        <option value="ja">日本語</option>
        <option value="ko">한국어</option>
        <option value="en">English</option>
      </select>
    </div>

    <!-- Messages -->
    <div id="messages" class="messages"></div>

    <!-- Phase progress -->
    <div id="phases" class="phases hidden"></div>

    <!-- Input -->
    <div class="input-area">
      <textarea id="queryInput" placeholder="Ask about OpenFrame..." rows="1"></textarea>
      <button id="btnSend" class="send-btn" title="Send (Ctrl+Enter)">
        <svg width="16" height="16" viewBox="0 0 16 16"><path fill="currentColor" d="M1 1.5l14 6.5-14 6.5V9l10-1-10-1V1.5z"/></svg>
      </button>
      <button id="btnCancel" class="cancel-btn hidden" title="Cancel">
        <svg width="16" height="16" viewBox="0 0 16 16"><rect fill="currentColor" x="3" y="3" width="10" height="10" rx="1"/></svg>
      </button>
    </div>
  </div>

  <script nonce="${nonce}" src="${chatJsUri}"></script>
</body>
</html>`;
  }
}

function getNonce(): string {
  const chars =
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  let result = "";
  for (let i = 0; i < 32; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
}
