{
  description = "Cardano Blockperf";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-23.11";
    flake-parts.url = "github:hercules-ci/flake-parts";
    poetry2nix.url = "github:nix-community/poetry2nix";
    poetry2nix.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = inputs @ {
    flake-parts,
    poetry2nix,
    ...
  }:
    flake-parts.lib.mkFlake {inherit inputs;} {
      systems = ["x86_64-linux" "x86_64-darwin"];
      perSystem = {
        config,
        pkgs,
        lib,
        system,
        ...
      }: {
        _module.args.pkgs = import inputs.nixpkgs {
          inherit system;
          overlays = [poetry2nix.overlays.default];
        };

        packages.blockperf = pkgs.poetry2nix.mkPoetryApplication {
          projectDir = ./.;
          checkGroups = [];
        };

        devShells.default = pkgs.mkShell {packages = [pkgs.poetry pkgs.python3];};
      };

      #   buildPythonApplication {
      #     pname = "blockperf";

      #     version = let
      #       initFile = readFile ./src/blockperf/__init__.py;
      #       initVersion = head (match ".*__version__ = \"([[:digit:]]\.[[:digit:]]\.[[:digit:]]).*" initFile);
      #     in "${initVersion}-${inputs.self.shortRev or "dirty"}";

      #     pyproject = true;
      #     src = ./.;

      #     propagatedBuildInputs = [
      #       paho-mqtt
      #       psutil
      #       setuptools
      #     ];

      #     meta = with lib; {
      #       description = "Cardano BlockPerf";
      #       homepage = "https://github.com/cardano-foundation/blockperf";
      #       license = licenses.mit;
      #     };
      #   };
      # };
    };
}
