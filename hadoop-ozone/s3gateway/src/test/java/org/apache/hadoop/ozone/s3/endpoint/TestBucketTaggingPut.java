/*
 * Licensed to the Apache Software Foundation (ASF) under one or more
 * contributor license agreements. See the NOTICE file distributed with
 * this work for additional information regarding copyright ownership.
 * The ASF licenses this file to You under the Apache License, Version 2.0
 * (the "License"); you may not use this file except in compliance with
 * the License. You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

package org.apache.hadoop.ozone.s3.endpoint;

import static org.apache.hadoop.ozone.s3.endpoint.EndpointTestUtils.assertErrorResponse;
import static org.apache.hadoop.ozone.s3.endpoint.EndpointTestUtils.assertSucceeds;
import static org.apache.hadoop.ozone.s3.endpoint.EndpointTestUtils.putBucketTagging;
import static org.apache.hadoop.ozone.s3.exception.S3ErrorTable.INVALID_TAG;
import static org.apache.hadoop.ozone.s3.exception.S3ErrorTable.MALFORMED_XML;
import static org.apache.hadoop.ozone.s3.exception.S3ErrorTable.NO_SUCH_BUCKET;
import static org.apache.hadoop.ozone.s3.util.S3Consts.STORAGE_CLASS_HEADER;
import static org.apache.hadoop.ozone.s3.util.S3Consts.TAG_BUCKET_NUM_LIMIT;
import static org.apache.hadoop.ozone.s3.util.S3Consts.TAG_KEY_LENGTH_LIMIT;
import static org.apache.hadoop.ozone.s3.util.S3Consts.TAG_VALUE_LENGTH_LIMIT;
import static org.apache.hadoop.ozone.s3.util.S3Consts.X_AMZ_CONTENT_SHA256;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.google.common.collect.ImmutableMap;
import java.util.Map;
import javax.ws.rs.core.HttpHeaders;
import org.apache.commons.lang3.StringUtils;
import org.apache.hadoop.ozone.client.OzoneClient;
import org.apache.hadoop.ozone.client.OzoneClientStub;
import org.apache.hadoop.ozone.s3.exception.OS3Exception;
import org.apache.hadoop.ozone.s3.util.S3Consts;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Tests for PutBucketTagging.
 */
public class TestBucketTaggingPut {

  private OzoneClient clientStub;
  private BucketEndpoint bucketEndpoint;

  private static final String BUCKET_NAME = "b1";
  private static final Map<String, String> TAGS = ImmutableMap.of("tag1", "val1", "tag2", "val2");

  @BeforeEach
  void setup() throws Exception {
    clientStub = new OzoneClientStub();
    clientStub.getObjectStore().createS3Bucket(BUCKET_NAME);

    HttpHeaders headers = mock(HttpHeaders.class);
    when(headers.getHeaderString(X_AMZ_CONTENT_SHA256)).thenReturn("UNSIGNED-PAYLOAD");
    when(headers.getHeaderString(STORAGE_CLASS_HEADER)).thenReturn("STANDARD");

    bucketEndpoint = EndpointBuilder.newBucketEndpointBuilder()
        .setClient(clientStub)
        .setHeaders(headers)
        .build();
  }

  @Test
  public void testPutBucketTaggingWithEmptyBody() {
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, ""));
  }

  @Test
  public void testPutValidBucketTagging() throws Exception {
    assertSucceeds(() -> putBucketTagging(bucketEndpoint, BUCKET_NAME, twoTags()));
    assertThat(clientStub.getObjectStore().getS3Bucket(BUCKET_NAME).getBucketTagging())
        .containsExactlyEntriesOf(TAGS);
  }

  @Test
  public void testPutInvalidBucketTagging() {
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, emptyBody()));
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, invalidXmlStructure()));
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, noTagSet()));
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, emptyTags()));
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, tagKeyNotSpecified()));
    assertErrorResponse(MALFORMED_XML, () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, tagValueNotSpecified()));
  }

  @Test
  public void testPutBucketTaggingDuplicateTagKey() {
    OS3Exception ex = assertErrorResponse(INVALID_TAG,
        () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, duplicateTagKeys()));
    assertThat(ex.getErrorMessage()).contains("There are tags with duplicate tag keys");
  }

  @Test
  public void testPutBucketTaggingLongTagKey() {
    String longTagKey = StringUtils.repeat('k', TAG_KEY_LENGTH_LIMIT + 1);
    OS3Exception ex = assertErrorResponse(INVALID_TAG,
        () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, singleTag(longTagKey, "value1")));
    assertThat(ex.getErrorMessage()).contains("The tag key exceeds the maximum length");
  }

  @Test
  public void testPutBucketTaggingLongTagValue() {
    String longTagValue = StringUtils.repeat('v', TAG_VALUE_LENGTH_LIMIT + 1);
    OS3Exception ex = assertErrorResponse(INVALID_TAG,
        () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, singleTag("tag1", longTagValue)));
    assertThat(ex.getErrorMessage()).contains("The tag value exceeds the maximum length");
  }

  @Test
  public void testPutBucketTaggingTooManyTags() {
    OS3Exception ex = assertErrorResponse(INVALID_TAG,
        () -> putBucketTagging(bucketEndpoint, BUCKET_NAME,
            taggingWithTagCount(TAG_BUCKET_NUM_LIMIT + 1)));
    assertThat(ex.getErrorMessage()).contains("exceeded the maximum number of tags");
  }

  @Test
  public void testPutBucketTaggingEmptyTagKey() {
    OS3Exception ex = assertErrorResponse(INVALID_TAG,
        () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, singleTag("", "value1")));
    assertThat(ex.getErrorMessage()).contains("Some tag keys are empty");
  }

  @Test
  public void testPutBucketTaggingAwsPrefixedKey() {
    OS3Exception ex = assertErrorResponse(INVALID_TAG,
        () -> putBucketTagging(bucketEndpoint, BUCKET_NAME, singleTag("aws:forbidden", "v")));
    assertThat(ex.getErrorMessage()).contains("aws:");
  }

  @Test
  public void testPutBucketTaggingNoBucketFound() {
    assertErrorResponse(NO_SUCH_BUCKET, () -> putBucketTagging(bucketEndpoint, "nonexistent", twoTags()));
  }

  private String duplicateTagKeys() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "      <Tag>" +
            "         <Key>tag1</Key>" +
            "         <Value>value1</Value>" +
            "      </Tag>" +
            "      <Tag>" +
            "         <Key>tag1</Key>" +
            "         <Value>value2</Value>" +
            "      </Tag>" +
            "   </TagSet>" +
            "</Tagging>";
  }

  private String singleTag(String key, String value) {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "      <Tag>" +
            "         <Key>" + escapeXml(key) + "</Key>" +
            "         <Value>" + escapeXml(value) + "</Value>" +
            "      </Tag>" +
            "   </TagSet>" +
            "</Tagging>";
  }

  private static String escapeXml(String s) {
    return s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\"", "&quot;");
  }

  private String taggingWithTagCount(int count) {
    StringBuilder sb = new StringBuilder();
    sb.append("<Tagging xmlns=\"").append(S3Consts.S3_XML_NAMESPACE).append("\">")
        .append("<TagSet>");
    for (int i = 0; i < count; i++) {
      sb.append("<Tag><Key>k").append(i).append("</Key><Value>v").append(i)
          .append("</Value></Tag>");
    }
    sb.append("</TagSet></Tagging>");
    return sb.toString();
  }

  private String emptyBody() {
    return null;
  }

  private String invalidXmlStructure() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "   </Ta" +
            "Tagging>";
  }

  private String twoTags() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "      <Tag>" +
            "         <Key>tag1</Key>" +
            "         <Value>val1</Value>" +
            "      </Tag>" +
            "      <Tag>" +
            "         <Key>tag2</Key>" +
            "         <Value>val2</Value>" +
            "      </Tag>" +
            "   </TagSet>" +
            "</Tagging>";
  }

  private String noTagSet() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "</Tagging>";
  }

  private String emptyTags() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "   </TagSet>" +
            "</Tagging>";
  }

  private String tagKeyNotSpecified() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "      <Tag>" +
            "         <Value>val1</Value>" +
            "      </Tag>" +
            "   </TagSet>" +
            "</Tagging>";
  }

  private String tagValueNotSpecified() {
    return
        "<Tagging xmlns=\"" + S3Consts.S3_XML_NAMESPACE + "\">" +
            "   <TagSet>" +
            "      <Tag>" +
            "         <Key>tag1</Key>" +
            "      </Tag>" +
            "   </TagSet>" +
            "</Tagging>";
  }
}
