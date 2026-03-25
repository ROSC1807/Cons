#include "deltasync.h"

#include <stdio.h>
#include <stdlib.h>
#include <time.h>

int main(void)
{
    const char server_file[] = "1.mp4";
    const char client_file[] = "test/1.mp4";
    const char temp_file[] = "temp";
    size_t server_info_len = 0;
    size_t client_message_len = 0;
    size_t message_len = 0;
    size_t server_offset = 0;
    size_t client_offset = 0;
    uint8_t message_buffer[DELTASYNC_MAX_MESSAGE_SIZE];
    uint8_t *server_file_info = NULL;
    uint8_t *client_message = NULL;
    DeltaSyncServerContext *server_context = NULL;
    bool syncing = true;
    clock_t start;
    clock_t end;
    int exit_code = EXIT_FAILURE;

    start = clock();

    server_file_info = serverReturnFileInfo(server_file, &server_info_len);
    if (server_file_info == NULL) {
        fprintf(stderr, "Failed to read server file info.\n");
        goto cleanup;
    }

    if (clientCompareFileInfo(client_file, server_file_info, server_info_len)) {
        printf("Client file is already up to date.\n");
        exit_code = EXIT_SUCCESS;
        goto cleanup;
    }

    client_message = clientTransform(client_file, &client_message_len);
    if (client_message == NULL && client_message_len != 0) {
        fprintf(stderr, "Failed to build client chunk signatures.\n");
        goto cleanup;
    }

    server_context = serverProcessMessage(client_message, client_message_len, server_file);
    if (server_context == NULL) {
        fprintf(stderr, "Failed to build DeltaSync server context.\n");
        goto cleanup;
    }

    if (!clientPrepareRebuildFile(client_file, temp_file)) {
        fprintf(stderr, "Failed to prepare client rebuild file.\n");
        goto cleanup;
    }

    printf("DeltaSync started.\n");
    while (syncing) {
        syncing = serverMainDeltaSync(server_context, message_buffer, server_file, &message_len, &server_offset);
        if (!clientRebuildFile(message_buffer, message_len, &client_offset, client_file, temp_file)) {
            if (syncing) {
                fprintf(stderr, "Failed to rebuild client file.\n");
                goto cleanup;
            }
            break;
        }
    }

    end = clock();
    printf("elapsed=%f\n", (double)(end - start) / CLOCKS_PER_SEC);
    exit_code = EXIT_SUCCESS;

cleanup:
    serverRecover(server_context);
    clientRecover(temp_file);
    free(client_message);
    free(server_file_info);
    return exit_code;
}
