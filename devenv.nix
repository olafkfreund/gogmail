{ pkgs, lib, config, ... }:

{
  # Package list
  packages = [
    pkgs.git
    pkgs.just
  ];

  # Language configuration
  languages.python = {
    enable = true;
    venv = {
      enable = true;
      requirements = ''
        textual>=0.50.0
        requests>=2.31.0
        rich>=13.7.0
      '';
    };
  };

  # Custom scripts
  scripts.run-app.exec = "PYTHONPATH=src python -m gogmail.app";
  scripts.run-tests.exec = "PYTHONPATH=src python -m unittest discover -s tests -v";
}
