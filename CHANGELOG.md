## [3.3.2](https://github.com/origox/syncbit/compare/v3.3.1...v3.3.2) (2026-01-01)


### Bug Fixes

* add tests for intraday to get ok coverage ([3cb6558](https://github.com/origox/syncbit/commit/3cb6558b05a508c6c640809bc3912a05aea299c8))

## [3.3.1](https://github.com/origox/syncbit/compare/v3.3.0...v3.3.1) (2026-01-01)


### Bug Fixes

* update python tests ([2d9df59](https://github.com/origox/syncbit/commit/2d9df59e2669543ccc8c653630d52a0ba04797ab))

## [3.3.0](https://github.com/origox/syncbit/compare/v3.2.0...v3.3.0) (2026-01-01)


### Features

* add comprehensive Charge 6 data collection with current-date sync ([dd7b3bd](https://github.com/origox/syncbit/commit/dd7b3bd2f09bf3f1fad6fb63b63977a8cd2c4fb8)), closes [#41](https://github.com/origox/syncbit/issues/41)
* implement intraday data backfill ([a5b4bbd](https://github.com/origox/syncbit/commit/a5b4bbd5b312a8022c7f9b1e80c796ae26c1af61))


### Bug Fixes

* correct API endpoint paths for new metrics ([49c985b](https://github.com/origox/syncbit/commit/49c985b2e1a6afed3f8a4c19a59ed0e8b089851d)), closes [#41](https://github.com/origox/syncbit/issues/41)
* correct sleep API endpoint to use v1.2 ([75c21c9](https://github.com/origox/syncbit/commit/75c21c990c5009a9b004bb588a00a73595d0b999)), closes [#41](https://github.com/origox/syncbit/issues/41)
* fix scheduler ([513667a](https://github.com/origox/syncbit/commit/513667aaaa3bf01fbe610c9ebba3a2e8c371b5ac))
* improve rate limit handling and backfill gap detection ([223c352](https://github.com/origox/syncbit/commit/223c352fa9dd7a5d0949fe360eaf627db247798a))
* prevent scheduled sync from blocking when quota exhausted ([d4c4cff](https://github.com/origox/syncbit/commit/d4c4cff923fbbc0bec9114888368cd5c16dd6d60))
* prevent state advancement when gap exists in backfill ([b3f782e](https://github.com/origox/syncbit/commit/b3f782ee9c08361f6998879d95f28585d77acc7c))
* trigger backfill automatically during scheduled sync when gap detected ([027e185](https://github.com/origox/syncbit/commit/027e185256500ba2f2e2b09474f8390dc6ad2d73))
* use Fitbit API quota reset time for intelligent backfill waiting ([efe1b23](https://github.com/origox/syncbit/commit/efe1b23918b9a04181713b74b08956ec31f39a94))


### Documentation

* update documentation for Charge 6 comprehensive data collection ([f4d0b94](https://github.com/origox/syncbit/commit/f4d0b949137948c54761ff901eb9a6fccf44e940)), closes [#41](https://github.com/origox/syncbit/issues/41)

## [3.2.0](https://github.com/origox/syncbit/compare/v3.1.0...v3.2.0) (2025-12-29)


### Features

* **helm:** add intraday data collection configuration ([9abee69](https://github.com/origox/syncbit/commit/9abee6910c659ffd4d653f15cd9efa13ea8cd777)), closes [#38](https://github.com/origox/syncbit/issues/38)

## [3.1.0](https://github.com/origox/syncbit/compare/v3.0.0...v3.1.0) (2025-12-29)


### Features

* add Fitbit intraday data collection ([#39](https://github.com/origox/syncbit/issues/39)) ([e444717](https://github.com/origox/syncbit/commit/e4447172073b7c7336a3ac2f2face744cf18cca1)), closes [#38](https://github.com/origox/syncbit/issues/38) [#38](https://github.com/origox/syncbit/issues/38) [#38](https://github.com/origox/syncbit/issues/38)

## [3.0.0](https://github.com/origox/syncbit/compare/v2.0.0...v3.0.0) (2025-12-29)


### ⚠ BREAKING CHANGES

* **deps:** Update dependency gh ( 1.4.0 → 2.83.2 ) (#37)

### Features

* **deps:** Update dependency gh ( 1.4.0 → 2.83.2 ) ([#37](https://github.com/origox/syncbit/issues/37)) ([aecc1d5](https://github.com/origox/syncbit/commit/aecc1d51333398703257154314d85c702ff5e948))

## [2.0.0](https://github.com/origox/syncbit/compare/v1.6.2...v2.0.0) (2025-12-29)


### ⚠ BREAKING CHANGES

* **deps:** Update dependency docker-client ( 20.10.2 → 29.1.2 ) (#36)
* **github-action:** Update action actions/upload-artifact ( v4 → v6 ) (#35)
* **github-action:** Update action actions/setup-node ( v4 → v6 ) (#34)

### Features

* **deps:** Update dependency docker-client ( 20.10.2 → 29.1.2 ) ([#36](https://github.com/origox/syncbit/issues/36)) ([ff394de](https://github.com/origox/syncbit/commit/ff394def177329f44ca0442b4b1853e88b3f8249))


### Continuous Integration

* **github-action:** Update action actions/setup-node ( v4 → v6 ) ([#34](https://github.com/origox/syncbit/issues/34)) ([22a8121](https://github.com/origox/syncbit/commit/22a8121b2de205dbb4eb98dd3229574ed67d1788))
* **github-action:** Update action actions/upload-artifact ( v4 → v6 ) ([#35](https://github.com/origox/syncbit/issues/35)) ([b72bedc](https://github.com/origox/syncbit/commit/b72bedcd2043af76faff6edb49ea6139cb6dd75f))

## [1.6.2](https://github.com/origox/syncbit/compare/v1.6.1...v1.6.2) (2025-12-29)


### Bug Fixes

* clean up renovate and versions. Require ci to pass when automerge prod PR from renovate ([641dd37](https://github.com/origox/syncbit/commit/641dd3727e382bc4562f24f915e91aa8d1f171b9))

## [1.6.1](https://github.com/origox/syncbit/compare/v1.6.0...v1.6.1) (2025-12-28)


### Bug Fixes

* **helm:** ensure chart appVersion matches Docker image version ([cb3f902](https://github.com/origox/syncbit/commit/cb3f9024da0d64dda6d6775fd0cc9bd07f66f7cb))

## [1.6.0](https://github.com/origox/syncbit/compare/v1.5.0...v1.6.0) (2025-12-28)


### Features

* **helm:** add OCI artifact support for Helm chart distribution ([#30](https://github.com/origox/syncbit/issues/30)) ([61a4c8f](https://github.com/origox/syncbit/commit/61a4c8f68e22944ec65cea24371df7c29ddd6b31)), closes [#29](https://github.com/origox/syncbit/issues/29)

## [1.5.0](https://github.com/origox/syncbit/compare/v1.4.0...v1.5.0) (2025-12-28)


### Features

* prep for helm deployment via argocd ([1b75935](https://github.com/origox/syncbit/commit/1b75935a4560780635d08042cd7a8ce00bbcc6a4))

## [1.4.0](https://github.com/origox/syncbit/compare/v1.3.1...v1.4.0) (2025-12-28)


### Features

* **ci:** add Docker build to release workflow for semantic version tags ([a6ed834](https://github.com/origox/syncbit/commit/a6ed834e2771ec5c7d311dbe304aeda75ef801c2))


### Documentation

* **helm:** update documentation for ESO configuration ([62f65e3](https://github.com/origox/syncbit/commit/62f65e372793c6e422d7af6443007c245265104f))

## [1.3.1](https://github.com/origox/syncbit/compare/v1.3.0...v1.3.1) (2025-12-28)


### Bug Fixes

* **helm:** update ExternalSecret to use correct API version and 1Password format ([54481ac](https://github.com/origox/syncbit/commit/54481acf32b3693439294789eec6b1e760b25dee))

## [1.3.0](https://github.com/origox/syncbit/compare/v1.2.0...v1.3.0) (2025-12-28)


### Features

* **helm:** add production-ready Helm chart for Kubernetes deployment ([#28](https://github.com/origox/syncbit/issues/28)) ([442c043](https://github.com/origox/syncbit/commit/442c0435adea9a634026070fbf1a299a51cd0e91)), closes [#27](https://github.com/origox/syncbit/issues/27)

## [1.2.0](https://github.com/origox/syncbit/compare/v1.1.0...v1.2.0) (2025-12-28)


### Features

* **renovate:** group Python version updates across Dockerfile and Devbox ([#26](https://github.com/origox/syncbit/issues/26)) ([af2bdd7](https://github.com/origox/syncbit/commit/af2bdd735a73906a584f3f76b7cda9c043dbc201))

## [1.1.0](https://github.com/origox/syncbit/compare/v1.0.0...v1.1.0) (2025-12-27)


### Features

* **ci:** implement semantic versioning with automated releases ([#24](https://github.com/origox/syncbit/issues/24)) ([be2f527](https://github.com/origox/syncbit/commit/be2f52741283a2d640951d80abe6d11c97bba882)), closes [#23](https://github.com/origox/syncbit/issues/23) [#23](https://github.com/origox/syncbit/issues/23)
* **ci:** implement semantic versioning with automated releases ([#25](https://github.com/origox/syncbit/issues/25)) ([ffe2a8e](https://github.com/origox/syncbit/commit/ffe2a8ee70bc1f11ef883c24abd0576f75ff089d)), closes [#23](https://github.com/origox/syncbit/issues/23) [#23](https://github.com/origox/syncbit/issues/23)

## 1.0.0 (2025-12-27)


### ⚠ BREAKING CHANGES

* **github-action:** Update action actions/setup-python ( v5 → v6 ) (#11)
* **github-action:** Update action codecov/codecov-action ( v4 → v5 ) (#12)
* **deps-pytest:** Update pytest ecosystem (#14)
* **deps-lint:** Update black ( 24.1.1 → 25.12.0 ) (#13)
* **github-action:** Update action actions/checkout ( v4 → v6 ) (#10)

### Features

* **ci:** implement semantic versioning with automated releases ([#24](https://github.com/origox/syncbit/issues/24)) ([be2f527](https://github.com/origox/syncbit/commit/be2f52741283a2d640951d80abe6d11c97bba882)), closes [#23](https://github.com/origox/syncbit/issues/23) [#23](https://github.com/origox/syncbit/issues/23)
* **ci:** setup Renovate for automated dependency updates ([#6](https://github.com/origox/syncbit/issues/6)) ([be5af4b](https://github.com/origox/syncbit/commit/be5af4b5fefeb4ce5a9ae62bdd8db9e37cf1f665)), closes [#5](https://github.com/origox/syncbit/issues/5) [#5](https://github.com/origox/syncbit/issues/5)
* **ci:** setup Renovate for automated dependency updates ([#9](https://github.com/origox/syncbit/issues/9)) ([476910d](https://github.com/origox/syncbit/commit/476910dbe50821e6a80029a14293c0984c37d3bb)), closes [#5](https://github.com/origox/syncbit/issues/5) [#5](https://github.com/origox/syncbit/issues/5)
* **container:** update image python ( 3.11 → 3.14 ) ([#16](https://github.com/origox/syncbit/issues/16)) ([fe3d2c7](https://github.com/origox/syncbit/commit/fe3d2c72e63550c2f3bf73b122abdda2bd170ed1))
* **deps-lint:** Update black ( 24.1.1 → 25.12.0 ) ([#13](https://github.com/origox/syncbit/issues/13)) ([fd2b538](https://github.com/origox/syncbit/commit/fd2b5383446ab93b81467606b1381914bdd31103))
* **deps-lint:** update ruff ( 0.1.15 → 0.14.10 ) ([#17](https://github.com/origox/syncbit/issues/17)) ([1cff514](https://github.com/origox/syncbit/commit/1cff5144430582919ae26a003ceeab75add0facc))
* **deps-pytest:** Update pytest ecosystem ([#14](https://github.com/origox/syncbit/issues/14)) ([c2c756a](https://github.com/origox/syncbit/commit/c2c756ad7806dd02701772d5be909485da3b476e))
* **deps-pytest:** update pytest-mock ( 3.12.0 → 3.15.1 ) ([#18](https://github.com/origox/syncbit/issues/18)) ([b09aeb6](https://github.com/origox/syncbit/commit/b09aeb6df78d76197fb1697f4c5be8507ea4e6c2))
* **deps:** update freezegun ( 1.4.0 → 1.5.5 ) ([#19](https://github.com/origox/syncbit/issues/19)) ([89c686d](https://github.com/origox/syncbit/commit/89c686d06e9193d6ef12d229ea01f11e74194ac0))
* **deps:** update responses ( 0.24.1 → 0.25.8 ) ([#20](https://github.com/origox/syncbit/issues/20)) ([0c0bd13](https://github.com/origox/syncbit/commit/0c0bd13eb3f6e0e21bb2f5c5af76f22e62063de0))
* implement GitHub Actions CI workflow ([#4](https://github.com/origox/syncbit/issues/4)) ([4ac8852](https://github.com/origox/syncbit/commit/4ac88526b4eb32438e8ee99160ffb1feae36d9db)), closes [#3](https://github.com/origox/syncbit/issues/3)
* initial app ([eb65475](https://github.com/origox/syncbit/commit/eb65475699afbe5557b7dc3054d56ce4d9f4225c))
* **k8s:** add initial manifests for prototyping ([6a42203](https://github.com/origox/syncbit/commit/6a422039c8690688f0dd236a7ae5b20ab412ba39))


### Bug Fixes

* **ci:** use local> prefix for Renovate local config extends ([b8a7666](https://github.com/origox/syncbit/commit/b8a7666fb29946edd7f80de6c4c5518252033406))
* formating of main.py ([83f6370](https://github.com/origox/syncbit/commit/83f6370b64a28173438591ec1e893eda88998bf9))
* **security:** add explicit permissions to Renovate workflow ([d13930b](https://github.com/origox/syncbit/commit/d13930bcac9d0ad534cb8158a39e775441a88302))
* **tests:** remove unused imports and simplify nested with statements in test_main.py ([9e609bf](https://github.com/origox/syncbit/commit/9e609bfeddd79e51c2849907a4ca7258da0aae8c))
* update paths for renovate ([4432b93](https://github.com/origox/syncbit/commit/4432b93409d7a27ee7f98eb8b2cca308d22e1328))
* update paths for renovate ([fa61902](https://github.com/origox/syncbit/commit/fa619020a6d3d9a3ed700ac5e21b6796f3aecd27))
* update paths for renovate ([b9f4f56](https://github.com/origox/syncbit/commit/b9f4f56c1e44bf3d5a69d68b4e1c92d3deec6561))
* update paths for renovate ([1b016e4](https://github.com/origox/syncbit/commit/1b016e40827f3eb05921651262c5c226d2704536))
* update paths for renovate ([241a159](https://github.com/origox/syncbit/commit/241a159e22c104ac32b3514dc9f164940038b0a9))
* update paths for renovate ([86a7c41](https://github.com/origox/syncbit/commit/86a7c41ebe5ff6f1c6252c21183b7a014dbfa9aa))
* update paths for renovate ([ef58689](https://github.com/origox/syncbit/commit/ef58689fa04554d446c0f6a09b654c78e0a1a2d9))
* update paths for renovate ([b59b8b6](https://github.com/origox/syncbit/commit/b59b8b671aff5ba532ea8dd084053dce394f715e))
* update paths for renovate ([7fba8cd](https://github.com/origox/syncbit/commit/7fba8cde8a7be81e2a5c83780640242c77fe3055))
* update paths for renovate ([63ba80b](https://github.com/origox/syncbit/commit/63ba80ba471e73612c2b939aafa22b0dd3db0ee5))


### Documentation

* add architecture documentation and GitHub templates ([65202ac](https://github.com/origox/syncbit/commit/65202ac1da2a6d2e21382a68abe973d50230995b))
* add testing section to README ([1415f40](https://github.com/origox/syncbit/commit/1415f40962f13956800dd94580e1791d7f8fd442))
* explain app structure ([7724d25](https://github.com/origox/syncbit/commit/7724d25d8aef302b41401a933e58581de611bfde))


### Continuous Integration

* **github-action:** Update action actions/checkout ( v4 → v6 ) ([#10](https://github.com/origox/syncbit/issues/10)) ([6b894a0](https://github.com/origox/syncbit/commit/6b894a0d6fc94cb9323b4fc5c6f34747eeb642ba))
* **github-action:** Update action actions/setup-python ( v5 → v6 ) ([#11](https://github.com/origox/syncbit/issues/11)) ([0993be9](https://github.com/origox/syncbit/commit/0993be944054a5a10afc15988cb58474025ce620))
* **github-action:** Update action codecov/codecov-action ( v4 → v5 ) ([#12](https://github.com/origox/syncbit/issues/12)) ([60a0ee0](https://github.com/origox/syncbit/commit/60a0ee02ddb37d73e1fd2b14b99cab8ea64418bd))
