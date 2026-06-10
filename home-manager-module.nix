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
      default = "gemini-2.5-flash";
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
  };

  config = mkIf cfg.enable (
    let
      # When a key file is provided, wrap the binary so it reads the secret at
      # runtime instead of baking it into the store.
      finalPackage =
        if cfg.geminiApiKeyFile != null then
          pkgs.symlinkJoin {
            name = "gogmail-wrapped";
            paths = [ cfg.package ];
            nativeBuildInputs = [ pkgs.makeWrapper ];
            postBuild = ''
              wrapProgram $out/bin/gogmail \
                --run 'export GEMINI_API_KEY="$(cat ${toString cfg.geminiApiKeyFile})"'
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
      ];

      warnings = optional (cfg.geminiApiKey != null) ''
        programs.gogmail.geminiApiKey writes your API key into the world-readable
        Nix store. Use programs.gogmail.geminiApiKeyFile (e.g. an agenix secret)
        instead.
      '';
    }
  );
}
