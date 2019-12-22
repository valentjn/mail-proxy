<?php

function fatalError($message="") {
  http_response_code(500);
  error_log($message);
  die($message);
}

function searchMessage($mailbox, $numberOfMessages, $uid) {
  $searchWindowSize = 20;
  $initialNumber = getNumber($uid);

  for ($i = -1; $i < $searchWindowSize; $i++) {
    if ($i == -1) {
      $number = $initialNumber;
    } elseif ($i % 2 == 0) {
      $number = $initialNumber - ($i + 2) / 2;
    } else {
      $number = $initialNumber + ($i + 1) / 2;
    }

    if (($number >= 1) && ($number <= $numberOfMessages) &&
        compareUids(getUid($mailbox, $number), $uid)) {
      return $number;
    }
  }

  fatalError("searchMessage failed: could not find message with UID $uid");
}

function getUid($mailbox, $number) {
  $headers = imap_headerinfo($mailbox, $number) or
      die("imap_headerinfo failed: " . imap_last_error());
  $headerHash = hash("sha256", $headers->subject);
  $headerHash = hash("sha256", $headerHash . $headers->fromaddress);
  $headerHash = hash("sha256", $headerHash . $headers->date);

  if (isset($headers->message_id)) {
    $headerHash = hash("sha256", $headerHash . $headers->message_id);
  }

  return "$number-$headerHash";
}

function getNumber($uid) {
  $array = explode("-", $uid, 2);
  return $array[0];
}

function compareUids($uid1, $uid2) {
  $array1 = explode("-", $uid1, 2);
  $array2 = explode("-", $uid2, 2);
  return ($array1[1] === $array2[1]);
}

if (!isset($_POST["request"])) fatalError();
$jsonRequest = json_decode($_POST["request"], true);
if ($jsonRequest === null) fatalError();

$jsonConfiguration = json_decode(file_get_contents("proxyConfiguration.json"), true);
if ($jsonConfiguration === null) fatalError();

if (!isset($jsonConfiguration["username"]) || !isset($jsonConfiguration["passwordHash"])) {
  fatalError();
}

if (!isset($jsonRequest["version"]) || ($jsonRequest["version"] !== "1.0")) fatalError();
if (!isset($jsonRequest["username"]) || !isset($jsonRequest["password"])) fatalError();
if ($jsonRequest["username"] !== $jsonConfiguration["username"]) fatalError();
if (!password_verify($jsonRequest["password"], $jsonConfiguration["passwordHash"])) fatalError();

$mailboxName = "{" . $jsonRequest["serverUrl"] . "/pop3/ssl}INBOX";
$mailbox = imap_open($mailboxName, $jsonRequest["serverUsername"], $jsonRequest["serverPassword"])
    or fatalError("imap_open failed: " . imap_last_error());
$mailboxStatus = imap_status($mailbox, $mailboxName, SA_MESSAGES)
    or fatalError("imap_status failed: " . imap_last_error());
$numberOfMessages = $mailboxStatus->messages;

try{
  if ($jsonRequest["method"] === "fetchNewMessages") {
    $batchSize = $jsonRequest["data"]["batchSize"];
    $newerThanUid = $jsonRequest["data"]["newerThanUid"];
    $responseData = array();
    $fromNumber = $numberOfMessages;

    if (isset($newerThanUid) && (($newerThanNumber = searchMessage($mailbox,
        $numberOfMessages, $newerThanUid)) !== false)) {
      $toNumber = $newerThanNumber;
    } else {
      $toNumber = $numberOfMessages - $batchSize;
    }

    for ($number = $fromNumber; $number > max($toNumber, 0); $number--) {
      $uid = getUid($mailbox, $number);
      $header = imap_fetchheader($mailbox, $number) or
          fatalError("imap_fetchheader failed: " . imap_last_error());
      array_push($responseData, array("uid" => $uid, "header" => base64_encode($header)));
    }
  } elseif ($jsonRequest["method"] === "fetchOldMessages") {
    $batchSize = $jsonRequest["data"]["batchSize"];
    $olderThanUid = $jsonRequest["data"]["olderThanUid"];
    $fromNumber = searchMessage($mailbox, $numberOfMessages, $olderThanUid);
    $responseData = array();

    if ($fromNumber !== false) {
      $fromNumber--;

      for ($number = $fromNumber; $number > max($fromNumber - $batchSize, 0); $number--) {
        $uid = getUid($mailbox, $number);
        $header = imap_fetchheader($mailbox, $number) or
            fatalError("imap_fetchheader failed: " . imap_last_error());
        array_push($responseData, array("uid" => $uid, "header" => base64_encode($header)));
      }
    }
  } elseif ($jsonRequest["method"] === "fetchMessageBody") {
    $number = searchMessage($mailbox, $numberOfMessages, $jsonRequest["data"]["uid"]);
    //imap_fetchheader($mailbox, $number, FT_PREFETCHTEXT);
    $responseData = base64_encode(imap_fetchbody($mailbox, $number, "")) or
        fatalError("imap_fetchbody failed: " . imap_last_error());
  } else {
    fatalError("unknown request method");
  }
} finally {
  imap_close($mailbox) or fatalError("imap_close failed: " . imap_last_error());
}

//error_log(print_r($responseData, true));
$response = array("version" => "1.0", "status" => 200, "data" => $responseData);
//error_log(print_r(json_encode($response), true));
$jsonResponse = json_encode($response) or
    fatalError("json_encode failed: " . json_last_error_msg());
echo $jsonResponse;

?>
