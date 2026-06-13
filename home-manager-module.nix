self: { config, lib, pkgs, ... }:

with lib;

let
  cfg = config.programs.gogmail;
  basePackage = self.packages.${pkgs.system}.gogmail;
in
{
  options.programs.gogmail = {
    enable = mkEnableOption "GogMail TUI client (per-user)";

    package = mkOption {
      type = types.package;
      default = basePackage;
      defaultText = literalExpression "gogmail.packages.\${system}.gogmail";
      description = "The gogmail package to use.";
    };

    geminiApiKeyFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "config.age.secrets.gemini-api-key.path";
      description = ''
        Path to a file containing the Gemini API key (e.g. an agenix/sops
        secret). Read at runtime via a wrapper, so the key is never written to
        the world-readable Nix store. Preferred over geminiApiKey.
      '';
    };

    geminiApiKey = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Gemini API key as a literal string. NOT secret-safe: it is written to
        the world-readable Nix store. Prefer geminiApiKeyFile.
      '';
    };

    defaultModel = mkOption {
      type = types.str;
      default = "gemini-3.5-flash";
      description = "GEMINI_MODEL_DEFAULT environment variable.";
    };

    defaultAccount = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Optional initial Google account ($GOG_ACCOUNT). The app lists all
        authenticated `gog` accounts and can switch between them at runtime, so
        this only sets the starting account.
      '';
    };

    zoomAccountId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Zoom Server-to-Server OAuth Account ID ($GOG_ZOOM_ACCOUNT_ID). An
        identifier, not a secret. Needed for the Zoom tab's "Create Meeting".
      '';
    };

    zoomClientId = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Zoom Server-to-Server OAuth Client ID ($GOG_ZOOM_CLIENT_ID). An
        identifier, not a secret.
      '';
    };

    zoomClientSecretFile = mkOption {
      type = types.nullOr types.path;
      default = null;
      example = literalExpression "config.age.secrets.zoom-client-secret.path";
      description = ''
        Path to a file containing the Zoom Client Secret (e.g. an agenix/sops
        secret). Read at runtime via a wrapper, so it never enters the
        world-readable Nix store. Preferred over zoomClientSecret.
      '';
    };

    zoomClientSecret = mkOption {
      type = types.nullOr types.str;
      default = null;
      description = ''
        Zoom Client Secret as a literal string. NOT secret-safe: written to the
        world-readable Nix store. Prefer zoomClientSecretFile.
      '';
    };
  };

  config = mkIf cfg.enable (
    let
      # Secrets that must be read at runtime (never baked into the store) are
      # exported by a thin wrapper. Each entry is one `--run` export line.
      runtimeExports =
        optional (cfg.geminiApiKeyFile != null)
          ''export GEMINI_API_KEY="$(cat ${toString cfg.geminiApiKeyFile})"''
        ++ optional (cfg.zoomClientSecretFile != null)
          ''export GOG_ZOOM_CLIENT_SECRET="$(cat ${toString cfg.zoomClientSecretFile})"'';

      finalPackage =
        if runtimeExports != [ ] then
          pkgs.symlinkJoin
            {
              name = "gogmail-wrapped";
              paths = [ cfg.package ];
              nativeBuildInputs = [ pkgs.makeWrapper ];
              postBuild = ''
                wrapProgram $out/bin/gogmail \
                  ${concatMapStringsSep " \\\n  " (e: "--run ${escapeShellArg e}") runtimeExports}
              '';
            }
        else cfg.package;
    in
    {
      home.packages = [ finalPackage ];

      home.sessionVariables = mkMerge [
        { GEMINI_MODEL_DEFAULT = cfg.defaultModel; }
        (mkIf (cfg.geminiApiKey != null) { GEMINI_API_KEY = cfg.geminiApiKey; })
        (mkIf (cfg.defaultAccount != null) { GOG_ACCOUNT = cfg.defaultAccount; })
        (mkIf (cfg.zoomAccountId != null) { GOG_ZOOM_ACCOUNT_ID = cfg.zoomAccountId; })
        (mkIf (cfg.zoomClientId != null) { GOG_ZOOM_CLIENT_ID = cfg.zoomClientId; })
        (mkIf (cfg.zoomClientSecret != null) { GOG_ZOOM_CLIENT_SECRET = cfg.zoomClientSecret; })
      ];

      warnings =
        optional (cfg.geminiApiKey != null) ''
          programs.gogmail.geminiApiKey writes your API key into the world-readable
          Nix store. Use programs.gogmail.geminiApiKeyFile (e.g. an agenix secret)
          instead.
        ''
        ++ optional (cfg.zoomClientSecret != null) ''
          programs.gogmail.zoomClientSecret writes your Zoom client secret into the
          world-readable Nix store. Use programs.gogmail.zoomClientSecretFile (e.g.
          an agenix secret) instead.
        '';
    }
  );
}
