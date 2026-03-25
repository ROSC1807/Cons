#include "deltasync.h"

#include <stdio.h>
#include <stdlib.h>
#include <sys/stat.h>

typedef struct HashNode {
    uint32_t chunk_id;
    uint32_t weak_checksum;
    uint8_t strong_checksum[STRONG_CHECKSUM_SIZE];
    struct HashNode *next;
} HashNode;

typedef struct HashBucket {
    size_t length;
    HashNode *head;
    HashNode *tail;
} HashBucket;

struct DeltaSyncServerContext {
    size_t file_size;
    HashBucket *buckets;
};

typedef struct {
    unsigned int count[2];
    unsigned int state[4];
    unsigned char buffer[64];
} MD5_CTX;

static void MD5Init(MD5_CTX *context);
static void MD5Update(MD5_CTX *context, const unsigned char *input, unsigned int inputlen);
static void MD5Final(MD5_CTX *context, unsigned char digest[STRONG_CHECKSUM_SIZE]);
static void MD5Transform(unsigned int state[4], const unsigned char block[64]);
static void MD5Encode(unsigned char *output, const unsigned int *input, unsigned int len);
static void MD5Decode(unsigned int *output, const unsigned char *input, unsigned int len);
static uint32_t adler32_roll(const uint8_t *buf, size_t len, size_t *cursor, uint32_t *s1, uint32_t *s2);
static bool append_bucket_node(HashBucket *bucket, uint32_t chunk_id, const uint8_t *chunk_message);
static void free_context(DeltaSyncServerContext *context);
static bool emit_literal_message(uint8_t *message, size_t *len, size_t literal_count, DeltaSyncMessageType type);

uint8_t *serverReturnFileInfo(const char *file_path, size_t *len)
{
    struct stat file_info;
    const char *file_name;
    size_t file_name_len;
    uint8_t *message;

    if (len == NULL || stat(file_path, &file_info) != 0) {
        return NULL;
    }

    file_name = deltasync_basename(file_path);
    file_name_len = strlen(file_name);
    *len = file_name_len + FILE_INFO_TRAILER_SIZE;

    message = (uint8_t *)malloc(*len);
    if (message == NULL) {
        *len = 0;
        return NULL;
    }

    memcpy(message, file_name, file_name_len);
    deltasync_write_u32_le(message + file_name_len, (uint32_t)file_info.st_size);
    deltasync_write_u32_le(message + file_name_len + WEAK_CHECKSUM_SIZE, (uint32_t)file_info.st_mtime);
    return message;
}

DeltaSyncServerContext *serverProcessMessage(const uint8_t *message, size_t len, const char *file_path)
{
    struct stat file_info;
    DeltaSyncServerContext *context;
    size_t chunk_count;
    size_t i;

    if ((message == NULL && len != 0) || stat(file_path, &file_info) != 0) {
        return NULL;
    }

    context = (DeltaSyncServerContext *)calloc(1, sizeof(*context));
    if (context == NULL) {
        return NULL;
    }

    context->buckets = (HashBucket *)calloc(HASH_BUCKET_COUNT, sizeof(*context->buckets));
    if (context->buckets == NULL) {
        free(context);
        return NULL;
    }

    context->file_size = (size_t)file_info.st_size;
    chunk_count = len / CHUNK_SIGNATURE_SIZE;
    for (i = 0; i < chunk_count; ++i) {
        const uint8_t *chunk_message = message + (i * CHUNK_SIGNATURE_SIZE);
        uint32_t weak_checksum = deltasync_read_u32_be(chunk_message);
        HashBucket *bucket = &context->buckets[weak_checksum % HASH_BUCKET_COUNT];

        if (!append_bucket_node(bucket, (uint32_t)i, chunk_message)) {
            free_context(context);
            return NULL;
        }
    }

    return context;
}

bool serverMainDeltaSync(DeltaSyncServerContext *context, uint8_t *message, const char *file_path, size_t *len, size_t *offset)
{
    FILE *in;
    uint8_t buffer[SLIDE_WINDOW_SIZE + CHUNK_SIZE];
    uint8_t strong_checksum[STRONG_CHECKSUM_SIZE] = {0};
    size_t cursor = 0;
    size_t bytes_in_buffer = 0;
    size_t fail_count = 0;
    uint32_t s1 = 0;
    uint32_t s2 = 0;
    bool first_window = true;

    if (context == NULL || message == NULL || len == NULL || offset == NULL) {
        return false;
    }

    in = fopen(file_path, "rb");
    if (in == NULL) {
        return false;
    }

    if (fseek(in, (long)*offset, SEEK_SET) != 0) {
        fclose(in);
        return false;
    }

    while (true) {
        if (*offset >= context->file_size) {
            bool result = emit_literal_message(message, len, fail_count, DELTASYNC_MSG_END);
            fclose(in);
            return result && false;
        }

        if (first_window) {
            bytes_in_buffer = fread(buffer, 1, sizeof(buffer), in);
            if (ferror(in)) {
                fclose(in);
                return false;
            }
            first_window = false;
        }

        if (bytes_in_buffer == 0) {
            bool result = emit_literal_message(message, len, fail_count, DELTASYNC_MSG_END);
            fclose(in);
            return result && false;
        }

        {
            uint32_t weak_checksum = adler32_roll(buffer, bytes_in_buffer, &cursor, &s1, &s2);
            size_t window_offset = cursor == 0 ? 0 : cursor - 1;
            HashBucket *bucket = &context->buckets[weak_checksum % HASH_BUCKET_COUNT];
            HashNode *node;
            bool matched = false;
            bool strong_ready = false;

            if (window_offset + CHUNK_SIZE > bytes_in_buffer || bucket->length == 0) {
                message[fail_count + 1] = buffer[window_offset];
                fail_count += 1;
                *offset += 1;
                if (fail_count == SLIDE_WINDOW_SIZE) {
                    bool result = emit_literal_message(message, len, fail_count, DELTASYNC_MSG_LITERAL_ONLY);
                    fclose(in);
                    return result;
                }
                continue;
            }

            for (node = bucket->head; node != NULL; node = node->next) {
                if (node->weak_checksum != weak_checksum) {
                    continue;
                }

                if (!strong_ready) {
                    MD5_CTX md5;
                    MD5Init(&md5);
                    MD5Update(&md5, buffer + window_offset, CHUNK_SIZE);
                    MD5Final(&md5, strong_checksum);
                    strong_ready = true;
                }

                if (memcmp(node->strong_checksum, strong_checksum, STRONG_CHECKSUM_SIZE) != 0) {
                    continue;
                }

                message[0] = fail_count > 0 ? DELTASYNC_MSG_LITERAL_AND_BLOCK : DELTASYNC_MSG_BLOCK_ONLY;
                deltasync_write_u32_be(message + 1 + fail_count, node->chunk_id);
                *len = fail_count + 1 + WEAK_CHECKSUM_SIZE;
                *offset += CHUNK_SIZE;
                fclose(in);
                matched = true;
                return matched;
            }

            message[fail_count + 1] = buffer[window_offset];
            fail_count += 1;
            *offset += 1;
            if (fail_count == SLIDE_WINDOW_SIZE) {
                bool result = emit_literal_message(message, len, fail_count, DELTASYNC_MSG_LITERAL_ONLY);
                fclose(in);
                return result;
            }
        }
    }
}

void serverRecover(DeltaSyncServerContext *context)
{
    free_context(context);
}

static bool append_bucket_node(HashBucket *bucket, uint32_t chunk_id, const uint8_t *chunk_message)
{
    HashNode *node = (HashNode *)calloc(1, sizeof(*node));

    if (node == NULL) {
        return false;
    }

    node->chunk_id = chunk_id;
    node->weak_checksum = deltasync_read_u32_be(chunk_message);
    memcpy(node->strong_checksum, chunk_message + WEAK_CHECKSUM_SIZE, STRONG_CHECKSUM_SIZE);

    if (bucket->head == NULL) {
        bucket->head = node;
        bucket->tail = node;
    } else {
        bucket->tail->next = node;
        bucket->tail = node;
    }

    bucket->length += 1;
    return true;
}

static void free_context(DeltaSyncServerContext *context)
{
    size_t i;

    if (context == NULL) {
        return;
    }

    if (context->buckets != NULL) {
        for (i = 0; i < HASH_BUCKET_COUNT; ++i) {
            HashNode *node = context->buckets[i].head;
            while (node != NULL) {
                HashNode *next = node->next;
                free(node);
                node = next;
            }
        }
        free(context->buckets);
    }

    free(context);
}

static bool emit_literal_message(uint8_t *message, size_t *len, size_t literal_count, DeltaSyncMessageType type)
{
    if (message == NULL || len == NULL) {
        return false;
    }

    message[0] = (uint8_t)type;
    *len = literal_count + 1;
    return true;
}

static uint32_t adler32_roll(const uint8_t *buf, size_t len, size_t *cursor, uint32_t *s1, uint32_t *s2)
{
    size_t i;

    if ((len - *cursor) <= CHUNK_SIZE) {
        *cursor += 1;
        return 0;
    }

    if (*cursor == 0) {
        for (i = 0; i + 4 <= CHUNK_SIZE; i += 4) {
            *s2 += 4 * (*s1 + buf[i]) + 3 * buf[i + 1] + 2 * buf[i + 2] + buf[i + 3];
            *s1 += buf[i] + buf[i + 1] + buf[i + 2] + buf[i + 3];
        }

        for (; i < CHUNK_SIZE; ++i) {
            *s1 += buf[i];
            *s2 += *s1;
        }
    } else {
        *s1 = *s1 - buf[*cursor - 1] + buf[*cursor + CHUNK_SIZE - 1];
        *s2 = *s2 - (CHUNK_SIZE * buf[*cursor - 1]);
        *s2 = *s2 + *s1;
    }

    *cursor += 1;
    return ((*s2 % MOD_ADLER) << 16) + (*s1 % MOD_ADLER);
}

#define F(x, y, z) ((x & y) | (~x & z))
#define G(x, y, z) ((x & z) | (y & ~z))
#define H(x, y, z) (x ^ y ^ z)
#define I(x, y, z) (y ^ (x | ~z))
#define ROTATE_LEFT(x, n) ((x << n) | (x >> (32 - n)))
#define FF(a, b, c, d, x, s, ac) \
    {                               \
        a += F(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }
#define GG(a, b, c, d, x, s, ac) \
    {                               \
        a += G(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }
#define HH(a, b, c, d, x, s, ac) \
    {                               \
        a += H(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }
#define II(a, b, c, d, x, s, ac) \
    {                               \
        a += I(b, c, d) + x + ac;   \
        a = ROTATE_LEFT(a, s);      \
        a += b;                     \
    }

static const unsigned char PADDING[] = {
    0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
};

static void MD5Init(MD5_CTX *context)
{
    context->count[0] = 0;
    context->count[1] = 0;
    context->state[0] = 0x67452301;
    context->state[1] = 0xEFCDAB89;
    context->state[2] = 0x98BADCFE;
    context->state[3] = 0x10325476;
}

static void MD5Update(MD5_CTX *context, const unsigned char *input, unsigned int inputlen)
{
    unsigned int i = 0;
    unsigned int index = (context->count[0] >> 3) & 0x3F;
    unsigned int partlen = 64 - index;

    context->count[0] += inputlen << 3;
    if (context->count[0] < (inputlen << 3)) {
        context->count[1]++;
    }
    context->count[1] += inputlen >> 29;

    if (inputlen >= partlen) {
        memcpy(&context->buffer[index], input, partlen);
        MD5Transform(context->state, context->buffer);
        for (i = partlen; i + 64 <= inputlen; i += 64) {
            MD5Transform(context->state, &input[i]);
        }
        index = 0;
    }

    memcpy(&context->buffer[index], &input[i], inputlen - i);
}

static void MD5Final(MD5_CTX *context, unsigned char digest[STRONG_CHECKSUM_SIZE])
{
    unsigned int bits[2] = {context->count[0], context->count[1]};
    unsigned int index = (context->count[0] >> 3) & 0x3F;
    unsigned int padlen = (index < 56) ? (56 - index) : (120 - index);
    unsigned char encoded_bits[8];

    MD5Encode(encoded_bits, bits, 8);
    MD5Update(context, PADDING, padlen);
    MD5Update(context, encoded_bits, 8);
    MD5Encode(digest, context->state, STRONG_CHECKSUM_SIZE);
}

static void MD5Encode(unsigned char *output, const unsigned int *input, unsigned int len)
{
    unsigned int i = 0;
    unsigned int j = 0;

    while (j < len) {
        output[j] = input[i] & 0xFF;
        output[j + 1] = (input[i] >> 8) & 0xFF;
        output[j + 2] = (input[i] >> 16) & 0xFF;
        output[j + 3] = (input[i] >> 24) & 0xFF;
        ++i;
        j += 4;
    }
}

static void MD5Decode(unsigned int *output, const unsigned char *input, unsigned int len)
{
    unsigned int i = 0;
    unsigned int j = 0;

    while (j < len) {
        output[i] = (unsigned int)input[j] |
                    ((unsigned int)input[j + 1] << 8) |
                    ((unsigned int)input[j + 2] << 16) |
                    ((unsigned int)input[j + 3] << 24);
        ++i;
        j += 4;
    }
}

static void MD5Transform(unsigned int state[4], const unsigned char block[64])
{
    unsigned int a = state[0];
    unsigned int b = state[1];
    unsigned int c = state[2];
    unsigned int d = state[3];
    unsigned int x[64];

    MD5Decode(x, block, 64);
    FF(a, b, c, d, x[0], 7, 0xd76aa478);
    FF(d, a, b, c, x[1], 12, 0xe8c7b756);
    FF(c, d, a, b, x[2], 17, 0x242070db);
    FF(b, c, d, a, x[3], 22, 0xc1bdceee);
    FF(a, b, c, d, x[4], 7, 0xf57c0faf);
    FF(d, a, b, c, x[5], 12, 0x4787c62a);
    FF(c, d, a, b, x[6], 17, 0xa8304613);
    FF(b, c, d, a, x[7], 22, 0xfd469501);
    FF(a, b, c, d, x[8], 7, 0x698098d8);
    FF(d, a, b, c, x[9], 12, 0x8b44f7af);
    FF(c, d, a, b, x[10], 17, 0xffff5bb1);
    FF(b, c, d, a, x[11], 22, 0x895cd7be);
    FF(a, b, c, d, x[12], 7, 0x6b901122);
    FF(d, a, b, c, x[13], 12, 0xfd987193);
    FF(c, d, a, b, x[14], 17, 0xa679438e);
    FF(b, c, d, a, x[15], 22, 0x49b40821);

    GG(a, b, c, d, x[1], 5, 0xf61e2562);
    GG(d, a, b, c, x[6], 9, 0xc040b340);
    GG(c, d, a, b, x[11], 14, 0x265e5a51);
    GG(b, c, d, a, x[0], 20, 0xe9b6c7aa);
    GG(a, b, c, d, x[5], 5, 0xd62f105d);
    GG(d, a, b, c, x[10], 9, 0x02441453);
    GG(c, d, a, b, x[15], 14, 0xd8a1e681);
    GG(b, c, d, a, x[4], 20, 0xe7d3fbc8);
    GG(a, b, c, d, x[9], 5, 0x21e1cde6);
    GG(d, a, b, c, x[14], 9, 0xc33707d6);
    GG(c, d, a, b, x[3], 14, 0xf4d50d87);
    GG(b, c, d, a, x[8], 20, 0x455a14ed);
    GG(a, b, c, d, x[13], 5, 0xa9e3e905);
    GG(d, a, b, c, x[2], 9, 0xfcefa3f8);
    GG(c, d, a, b, x[7], 14, 0x676f02d9);
    GG(b, c, d, a, x[12], 20, 0x8d2a4c8a);

    HH(a, b, c, d, x[5], 4, 0xfffa3942);
    HH(d, a, b, c, x[8], 11, 0x8771f681);
    HH(c, d, a, b, x[11], 16, 0x6d9d6122);
    HH(b, c, d, a, x[14], 23, 0xfde5380c);
    HH(a, b, c, d, x[1], 4, 0xa4beea44);
    HH(d, a, b, c, x[4], 11, 0x4bdecfa9);
    HH(c, d, a, b, x[7], 16, 0xf6bb4b60);
    HH(b, c, d, a, x[10], 23, 0xbebfbc70);
    HH(a, b, c, d, x[13], 4, 0x289b7ec6);
    HH(d, a, b, c, x[0], 11, 0xeaa127fa);
    HH(c, d, a, b, x[3], 16, 0xd4ef3085);
    HH(b, c, d, a, x[6], 23, 0x04881d05);
    HH(a, b, c, d, x[9], 4, 0xd9d4d039);
    HH(d, a, b, c, x[12], 11, 0xe6db99e5);
    HH(c, d, a, b, x[15], 16, 0x1fa27cf8);
    HH(b, c, d, a, x[2], 23, 0xc4ac5665);

    II(a, b, c, d, x[0], 6, 0xf4292244);
    II(d, a, b, c, x[7], 10, 0x432aff97);
    II(c, d, a, b, x[14], 15, 0xab9423a7);
    II(b, c, d, a, x[5], 21, 0xfc93a039);
    II(a, b, c, d, x[12], 6, 0x655b59c3);
    II(d, a, b, c, x[3], 10, 0x8f0ccc92);
    II(c, d, a, b, x[10], 15, 0xffeff47d);
    II(b, c, d, a, x[1], 21, 0x85845dd1);
    II(a, b, c, d, x[8], 6, 0x6fa87e4f);
    II(d, a, b, c, x[15], 10, 0xfe2ce6e0);
    II(c, d, a, b, x[6], 15, 0xa3014314);
    II(b, c, d, a, x[13], 21, 0x4e0811a1);
    II(a, b, c, d, x[4], 6, 0xf7537e82);
    II(d, a, b, c, x[11], 10, 0xbd3af235);
    II(c, d, a, b, x[2], 15, 0x2ad7d2bb);
    II(b, c, d, a, x[9], 21, 0xeb86d391);

    state[0] += a;
    state[1] += b;
    state[2] += c;
    state[3] += d;
}
