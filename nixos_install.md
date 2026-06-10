# NixOS Installation & Configuration Guide for GogMail

This guide explains how to install and configure GogMail on NixOS using the provided flake and NixOS module options.

## 1. Prerequisites

Before installing GogMail, make sure the `gog` CLI client (`gogcli`) is installed and authenticated. GogMail relies on the `gog` command to interact with Google Workspace APIs.

To authenticate your account via the browser OAuth flow, run:
```bash
gog auth add your-email@example.com --services gmail,calendar,contacts,tasks,drive,chat
```
Verify that the default account is set and your credentials work:
```bash
gog auth list
gog contacts list
```

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
2. Set up global environment variables (`GEMINI_API_KEY`, `GEMINI_MODEL_DEFAULT`, and `GOG_ACCOUNT`).

## 4. Running the Application

After applying your configuration with `nixos-rebuild switch`, you can launch the client directly from your terminal:

```bash
gogmail
```

### Configuration & Themes
GogMail stores user-level settings (such as the active theme) inside your home directory:
`~/.config/gogmail/settings.json`

You can change the active theme by navigating to **Settings** -> **Select Theme** inside the TUI application.
