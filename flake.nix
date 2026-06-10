{
  description = "Modern TUI Client for Google Workspace using gog CLI and Gemini";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;

        # nixpkgs ships gogcli 0.11; GogMail relies on 0.19 features (e.g.
        # `gmail thread get`). Pin 0.19.0 from upstream.
        gogcli = pkgs.gogcli.overrideAttrs (old: {
          version = "0.19.0";
          src = pkgs.fetchFromGitHub {
            owner = "steipete";
            repo = "gogcli";
            rev = "v0.19.0";
            hash = "sha256-8+ojZUNsmAzFQbdTG0eE/FG6nbptq49QZqjrCP1RhE4=";
          };
          vendorHash = "sha256-fkvMTJmYRsknDDffrZq2L2GRYDozwPX0yv7K84n5a84=";
        });

        # Tools GogMail shells out to at runtime. `gog` is required; the rest are
        # optional (clipboard, browser, image preview) but wired onto PATH so the
        # packaged app works out of the box. --suffix is used when wrapping so a
        # user's own (possibly newer) gog on PATH still takes precedence.
        runtimeDeps = [
          gogcli
          pkgs.wl-clipboard
          pkgs.xclip
          pkgs.xsel
          pkgs.xdg-utils
          pkgs.timg
        ];

        gogmail = python.pkgs.buildPythonApplication {
          pname = "gogmail";
          version = "0.1.0";
          src = ./.;
          pyproject = true;

          nativeBuildInputs = [ pkgs.makeWrapper ];
          build-system = [ python.pkgs.setuptools ];
          dependencies = with python.pkgs; [ textual requests rich ];

          # The unit suite is stdlib unittest and hermetic (gog/network mocked).
          doCheck = true;
          checkPhase = ''
            runHook preCheck
            # Use the build env's python (which has textual/requests/rich on
            # PYTHONPATH); prepend src rather than overriding PYTHONPATH.
            PYTHONPATH=$PWD/src:$PYTHONPATH python -m unittest discover -s tests -v
            runHook postCheck
          '';

          postFixup = ''
            wrapProgram $out/bin/gogmail \
              --suffix PATH : ${pkgs.lib.makeBinPath runtimeDeps}
          '';

          meta = {
            description = "Modern TUI Client for Google Workspace using gog CLI and Gemini";
            homepage = "https://github.com/olafkfreund/gogmail";
            license = pkgs.lib.licenses.mit;
            mainProgram = "gogmail";
          };
        };
      in
      {
        packages.gogmail = gogmail;
        packages.default = gogmail;

        # `nix run github:olafkfreund/gogmail`
        apps.gogmail = flake-utils.lib.mkApp { drv = gogmail; };
        apps.default = self.apps.${system}.gogmail;

        # `nix develop`
        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps: with ps; [ textual requests rich ]))
            pkgs.just
          ] ++ runtimeDeps;
          shellHook = ''
            export PYTHONPATH=$PWD/src:''${PYTHONPATH:-}
            echo "GogMail dev shell — run 'python -m gogmail.app' or 'just test'"
          '';
        };

        # `nix flake check` builds the package, which runs the test suite.
        checks.gogmail = gogmail;
        checks.default = gogmail;
      }
    ) // {
      nixosModules.gogmail = import ./nixos-module.nix self;
      nixosModules.default = self.nixosModules.gogmail;

      homeManagerModules.gogmail = import ./home-manager-module.nix self;
      homeManagerModules.default = self.homeManagerModules.gogmail;
    };
}
