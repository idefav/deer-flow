# Harness Stateless Object Storage Review: Runtime Entry Points

Date: 2026-06-21

## Scope

This batch removes object-mode local `.deer-flow` runtime file assumptions from middleware, built-in tools, IM channel file paths, Feishu resource downloads, and the embedded Python client.

## Findings

- `ThreadDataMiddleware` returns virtual `/mnt/user-data/...` paths in object mode and no longer creates thread directories.
- `UploadsMiddleware` lists current and historical uploads from `ArtifactStore`.
- `present_files` validates object-mode virtual output paths without resolving local paths.
- `view_image` reads object bytes and keeps existing extension, size, MIME, and magic-byte validation.
- Channel inbound files write object uploads; outbound artifacts are materialized to temporary files for existing adapters.
- Feishu resource downloads write object uploads and skip local sandbox sync in object mode.
- `DeerFlowClient` upload/list/delete/get_artifact uses `ArtifactStore` in object mode while filesystem mode remains unchanged.

## Verification

```bash
uv --directory backend run pytest tests/test_thread_data_middleware.py tests/test_uploads_middleware_core_logic.py tests/test_present_file_tool_core_logic.py -q
uv --directory backend run pytest tests/test_channels.py::TestChannelManager::test_ingest_inbound_files_object_runtime_writes_artifact_store tests/test_channels.py::TestExtractArtifacts::test_resolve_attachments_object_runtime_reads_artifact_store -q
uv --directory backend run pytest tests/test_feishu_parser.py::test_feishu_receive_single_file_object_runtime_writes_artifact_store tests/test_feishu_parser.py::test_feishu_receive_file_replaces_placeholders_in_order -q
uv --directory backend run pytest tests/test_view_image_tool.py::test_view_image_object_runtime_reads_from_artifact_store tests/test_view_image_tool.py::test_view_image_reads_virtual_uploads_path -q
uv --directory backend run pytest tests/test_client.py::TestUploads::test_upload_files tests/test_client.py::TestUploads::test_list_uploads tests/test_client.py::TestUploads::test_delete_upload tests/test_client.py::TestUploads::test_object_runtime_upload_list_delete_use_artifact_store tests/test_client.py::TestArtifacts::test_get_artifact tests/test_client.py::TestArtifacts::test_object_runtime_get_artifact_reads_artifact_store -q
uv --directory backend run ruff check packages/harness/deerflow/agents/middlewares/thread_data_middleware.py packages/harness/deerflow/agents/middlewares/uploads_middleware.py packages/harness/deerflow/tools/builtins/present_file_tool.py packages/harness/deerflow/tools/builtins/view_image_tool.py app/channels/manager.py app/channels/feishu.py packages/harness/deerflow/client.py tests/test_thread_data_middleware.py tests/test_uploads_middleware_core_logic.py tests/test_present_file_tool_core_logic.py tests/test_view_image_tool.py tests/test_channels.py tests/test_feishu_parser.py tests/test_client.py
```

Result:

```text
Focused tests passed.
All checks passed!
```

## Remaining Risks

- Object-mode `view_image` reads committed object-store bytes. Mid-turn sandbox-generated images depend on sandbox flush timing or future explicit sandbox pull-through.
- Channel outbound attachments use temporary files so existing adapters can stay unchanged; operational cleanup relies on OS temp cleanup.
- Full live evidence in the target object-store/provisioner environment is still required.
