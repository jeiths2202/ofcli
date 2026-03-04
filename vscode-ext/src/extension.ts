import * as vscode from "vscode";
import { ChatViewProvider } from "./panels/ChatViewProvider";

export function activate(context: vscode.ExtensionContext) {
  const provider = new ChatViewProvider(context.extensionUri);

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("ofkms.chatView", provider, {
      webviewOptions: { retainContextWhenHidden: true },
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("ofkms.clearChat", () => {
      provider.clearChat();
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand("ofkms.checkHealth", () => {
      provider.checkHealth();
    })
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("ofkms")) {
        provider.onConfigChanged();
      }
    })
  );
}

export function deactivate() {}
