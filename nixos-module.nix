self: { config, lib, pkgs, ... }:

with lib;

let
  cfg = config.programs.gogmail;
in
{
  options.programs.gogmail = {
    enable = mkEnableOption "GogMail TUI client";

    geminiApiKey = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "GEMINI_API_KEY environment variable.";
    };

    defaultModel = mkOption {
      type = types.str;
      default = "gemini-2.5-flash";
      description = "GEMINI_MODEL_DEFAULT environment variable.";
    };

    defaultAccount = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = "GOG_ACCOUNT environment variable.";
    };
  };

  config = mkIf cfg.enable {
    # Install gogmail package
    environment.systemPackages = [
      self.packages.${pkgs.system}.gogmail
    ];

    # Set up global environment variables
    environment.sessionVariables = mkMerge [
      (mkIf (cfg.geminiApiKey != null) { GEMINI_API_KEY = cfg.geminiApiKey; })
      { GEMINI_MODEL_DEFAULT = cfg.defaultModel; }
      (mkIf (cfg.defaultAccount != null) { GOG_ACCOUNT = cfg.defaultAccount; })
    ];
  };
}
