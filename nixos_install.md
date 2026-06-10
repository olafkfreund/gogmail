# NixOS Installation & Configuration Guide for GogMail

This guide explains how to install and configure GogMail on NixOS using the provided flake and NixOS module options.

## 0. Quick try (no install)

```bash
nix run github:olafkfreund/gogmail        # run the app
nix develop github:olafkfreund/gogmail    # dev shell (python + gog + tools)
nix flake check github:olafkfreund/gogmail  # build + run the test suite
```

The packaged app wraps `gog` and the clipboard/browser/image tools onto its
`PATH`, so it works without installing them separately (a `gog` already on your
`PATH` still takes precedence).

## 1. Prerequisites

GogMail talks to Google Workspace through the `gog` CLI (`gogcli`), which owns
authentication. Authenticate one or more accounts via the browser OAuth flow:

```bash
gog auth add your-email@example.com   --services gmail,calendar,contacts,tasks,drive,chat
gog auth add second-account@gmail.com --services gmail,calendar,contacts,tasks,drive,chat
gog auth list        # shows all stored accounts
```

## Using multiple Google accounts

GogMail reads every authenticated account from `gog auth list`. In the app, open
the **👤 Accounts** node in the sidebar and pick an account to switch — the
active account is shown in the status bar, persisted to
`~/.config/gogmail/settings.json`, and every view reloads under it. Set an
initial account with `defaultAccount`/`$GOG_ACCOUNT`, or leave it unset and the
app uses the first authenticated account.

## 2. Incorporating the Flake

Add the `gogmail` repository to your flake inputs in your NixOS configuration flake (usually `flake.nix` in `/etc/nixos/` or your dotfiles):

```nix
inputs = {
  # ... other inputs ...
  
  gogmail = {
    url = "git+https://github.com/olafkfreund/gogmail.git"; # Or local path: "git+file:///path/to/gogmail"
    inputs.nixpkgs.follows = "nixpkgs";
  };
};
```

Pass the `gogmail` input to your outputs function so it is available to your NixOS configuration modules:

```nix
outputs = { self, nixpkgs, gogmail, ... }@inputs: {
  nixosConfigurations.my-system = nixpkgs.lib.nixosSystem {
    system = "x86_64-linux";
    modules = [
      ./configuration.nix
      gogmail.nixosModules.default # Imports the GogMail NixOS module
    ];
  };
};
```

## 3. Configuring the NixOS Module

Once the module is imported, you can enable and configure GogMail using the declared options in your `configuration.nix` file:

```nix
programs.gogmail = {
  enable = true;
  
  # Configuration parameters (propagated as environment variables)
  geminiApiKey = "AIzaSy..."; # Your Gemini API key
  defaultModel = "gemini-2.5-flash"; # Default Gemini model
  defaultAccount = "your-email@example.com"; # Default Google Workspace account
};
```

This module will automatically:
1. Build and install the `gogmail` package globally (making the `gogmail` command available).
2. Set up global environment variables (`GEMINI_MODEL_DEFAULT`, and optionally `GOG_ACCOUNT`).

### Secret-safe API key (recommended)

`geminiApiKey` as a literal string is written to the **world-readable Nix
store**. Prefer `geminiApiKeyFile`, which is read at runtime via a wrapper:

```nix
programs.gogmail = {
  enable = true;
  geminiApiKeyFile = config.age.secrets.gemini-api-key.path; # agenix/sops
  defaultModel = "gemini-2.5-flash";
};
```

### Home Manager (per-user) module

GogMail is a per-user app (config, themes, active account, API key all live in
your home). The Home Manager module fits it better than the system module:

```nix
# flake.nix outputs -> homeConfigurations / HM as a flake module
imports = [ gogmail.homeManagerModules.default ];

programs.gogmail = {
  enable = true;
  geminiApiKeyFile = config.age.secrets.gemini-api-key.path;
  defaultModel = "gemini-2.5-flash";
  # defaultAccount left unset → switch accounts in-app
};
```

## 4. Running the Application

After applying your configuration with `nixos-rebuild switch`, you can launch the client directly from your terminal:

```bash
gogmail
```

### Configuration & Themes
GogMail stores user-level settings (such as the active theme) inside your home directory:
`~/.config/gogmail/settings.json`

You can change the active theme by navigating to **Settings** -> **Select Theme** inside the TUI application.
