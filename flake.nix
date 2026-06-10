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
        pythonPackages = pkgs.python3Packages;
      in
      {
        packages.gogmail = pythonPackages.buildPythonApplication {
          pname = "gogmail";
          version = "0.1.0";
          src = ./.;
          
          pyproject = true;

          build-system = [
            pythonPackages.setuptools
          ];

          dependencies = [
            pythonPackages.textual
            pythonPackages.requests
            pythonPackages.rich
          ];

          meta = {
            description = "Modern TUI Client for Google Workspace using gog CLI and Gemini";
            homepage = "https://github.com/olafkfreund/gogmail";
            license = pkgs.lib.licenses.mit;
          };
        };

        packages.default = self.packages.${system}.gogmail;
      }
    ) // {
      nixosModules.gogmail = import ./nixos-module.nix self;
      nixosModules.default = self.nixosModules.gogmail;
    };
}
